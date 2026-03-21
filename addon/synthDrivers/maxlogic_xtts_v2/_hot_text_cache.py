import hashlib
import json
import threading
import time


class HotTextCache(object):
	MIN_CHARS = 81
	MAX_CHARS = 1600
	MAX_AUDIO_BYTES = 6 * 1024 * 1024

	def __init__(self, ttl_seconds=60, max_bytes=96 * 1024 * 1024):
		self.ttl_seconds = max(5, int(ttl_seconds))
		self.max_bytes = max(8 * 1024 * 1024, int(max_bytes))
		self._lock = threading.Lock()
		self._entries = {}
		self._total_bytes = 0

	def get_audio(self, voice, speed, volume, language, text):
		cache_key = self._make_key(voice, speed, volume, language, text)
		now = time.time()
		with self._lock:
			self._prune_locked(now)
			entry = self._entries.get(cache_key)
			if entry is None:
				return None
			entry["expiresAt"] = now + self.ttl_seconds
			entry["lastUsed"] = now
			entry["useCount"] += 1
			return entry["audio"]

	def put_audio(self, voice, speed, volume, language, text, audio_bytes):
		if not self._is_cacheable_text(text):
			return
		if audio_bytes is None or len(audio_bytes) > self.MAX_AUDIO_BYTES:
			return
		cache_key = self._make_key(voice, speed, volume, language, text)
		now = time.time()
		with self._lock:
			self._prune_locked(now)
			existing = self._entries.pop(cache_key, None)
			if existing is not None:
				self._total_bytes -= len(existing["audio"])
			self._entries[cache_key] = {
				"audio": audio_bytes,
				"expiresAt": now + self.ttl_seconds,
				"lastUsed": now,
				"useCount": 1,
				"audioBytes": len(audio_bytes),
			}
			self._total_bytes += len(audio_bytes)
			self._prune_locked(now)

	def get_stats(self):
		now = time.time()
		with self._lock:
			self._prune_locked(now)
			return {
				"entryCount": len(self._entries),
				"sizeBytes": self._total_bytes,
				"ttlSeconds": self.ttl_seconds,
				"maxBytes": self.max_bytes,
			}

	def clear(self):
		with self._lock:
			self._entries = {}
			self._total_bytes = 0

	def _is_cacheable_text(self, text):
		if not text or not any(char.isalnum() for char in text):
			return False
		text_length = len(text)
		return self.MIN_CHARS <= text_length <= self.MAX_CHARS

	def _prune_locked(self, now):
		expired_keys = [key for key, entry in self._entries.items() if entry["expiresAt"] <= now]
		for key in expired_keys:
			entry = self._entries.pop(key, None)
			if entry is not None:
				self._total_bytes -= len(entry["audio"])
		if self._total_bytes <= self.max_bytes:
			return
		def score(item):
			key, entry = item
			return (entry["useCount"], entry["lastUsed"], entry["audioBytes"])
		for key, entry in sorted(self._entries.items(), key=score):
			if self._total_bytes <= self.max_bytes:
				break
			self._entries.pop(key, None)
			self._total_bytes -= len(entry["audio"])

	def _make_key(self, voice, speed, volume, language, text):
		payload = {
			"voice": voice or "",
			"speed": round(float(speed), 4),
			"volume": round(float(volume), 4),
			"language": language or "",
			"text": text,
		}
		return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
