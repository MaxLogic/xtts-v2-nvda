# Shared with kokoro-tts-nvda. Source of truth: xtts-v2-nvda/addon/synthDrivers/maxlogic_xtts_v2/_speech_cache.py.
# Change it there first, then copy it to kokoro. Only product names may differ.
import hashlib
import json
import os
import sqlite3
import threading
import time

try:
	from ._cache_settings import get_cache_settings_path, load_cache_settings, resolve_cache_policy
	from ._paths import get_cache_dir
except ImportError:
	from _cache_settings import get_cache_settings_path, load_cache_settings, resolve_cache_policy
	from _paths import get_cache_dir


class SpeechCache(object):
	MAX_AUDIO_BYTES = 2 * 1024 * 1024

	def __init__(self, logger):
		self._logger = logger
		self._lock = threading.Lock()
		self._db_path = os.path.join(get_cache_dir(create=True), "speech-cache.sqlite3")
		self._settings_path = get_cache_settings_path()
		self._settings_mtime = None
		self._policy = resolve_cache_policy(load_cache_settings())
		self._conn = None
		try:
			self._open()
		except sqlite3.DatabaseError as error:
			# The cache holds nothing that cannot be synthesized again.
			self._logger.warning("MaxLogic XTTS v2 speech cache is damaged and will be recreated: %s", error)
			self._set_aside_damaged_database()
			self._open()
		self._writes_since_prune = 0

	def _open(self):
		self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
		try:
			self._conn.execute("PRAGMA journal_mode=WAL")
			self._conn.execute("PRAGMA synchronous=NORMAL")
			self._ensure_schema()
		except Exception:
			self._conn.close()
			self._conn = None
			raise

	def _set_aside_damaged_database(self):
		os.replace(self._db_path, self._db_path + ".damaged")
		for suffix in ("-wal", "-shm"):
			try:
				os.remove(self._db_path + suffix)
			except OSError:
				pass

	def close(self):
		with self._lock:
			if self._conn is not None:
				self._conn.close()
				self._conn = None

	@property
	def db_path(self):
		return self._db_path

	def get_audio(self, voice, speed, volume, language, text):
		cache_key = self._make_key(voice, speed, volume, language, text)
		now = time.time()
		with self._lock:
			self._refresh_policy_locked()
			if not self._is_cacheable_text_locked(text):
				return None
			row = self._conn.execute(
				"SELECT audio FROM speech_cache WHERE cache_key = ?",
				(cache_key,),
			).fetchone()
			if row is None:
				return None
			self._conn.execute(
				"""
				UPDATE speech_cache
				SET use_count = use_count + 1,
					last_used = ?
				WHERE cache_key = ?
				""",
				(now, cache_key),
			)
			self._conn.commit()
		self._logger.debug("MaxLogic XTTS v2 cache hit. chars=%s voice=%s speed=%s", len(text), voice, speed)
		return row[0]

	def put_audio(self, voice, speed, volume, language, text, audio_bytes):
		if audio_bytes is None or len(audio_bytes) > self.MAX_AUDIO_BYTES:
			return
		cache_key = self._make_key(voice, speed, volume, language, text)
		now = time.time()
		with self._lock:
			self._refresh_policy_locked()
			if not self._is_cacheable_text_locked(text):
				return
			existing = self._conn.execute(
				"SELECT use_count, created_at FROM speech_cache WHERE cache_key = ?",
				(cache_key,),
			).fetchone()
			use_count = existing[0] if existing is not None else 0
			created_at = existing[1] if existing is not None else now
			self._conn.execute(
				"""
				INSERT OR REPLACE INTO speech_cache (
					cache_key,
					voice,
					speed,
					volume,
					language,
					text,
					audio,
					audio_bytes,
					use_count,
					last_used,
					created_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					cache_key,
					voice or "",
					round(float(speed), 4),
					round(float(volume), 4),
					language or "",
					text,
					sqlite3.Binary(audio_bytes),
					len(audio_bytes),
					max(use_count, 1),
					now,
					created_at,
				),
			)
			self._conn.commit()
			self._writes_since_prune += 1
			if self._writes_since_prune >= 8:
				self._prune_locked()
				self._writes_since_prune = 0

	def get_stats(self):
		with self._lock:
			self._refresh_policy_locked()
			row = self._conn.execute(
				"SELECT COUNT(*), COALESCE(SUM(audio_bytes), 0), COALESCE(MAX(last_used), 0) FROM speech_cache"
			).fetchone()
			count, total_bytes, last_used = row
			return {
				"dbPath": self._db_path,
				"entryCount": int(count or 0),
				"sizeBytes": int(total_bytes or 0),
				"sizeMb": round((total_bytes or 0) / float(1024 * 1024), 2),
				"lastUsed": last_used or None,
				"settings": dict(self._policy),
			}

	def clear(self):
		with self._lock:
			self._conn.execute("DELETE FROM speech_cache")
			self._conn.commit()

	def compact(self):
		with self._lock:
			self._conn.execute("VACUUM")
			self._conn.commit()

	def _ensure_schema(self):
		self._conn.executescript(
			"""
			CREATE TABLE IF NOT EXISTS speech_cache (
				cache_key TEXT PRIMARY KEY,
				voice TEXT NOT NULL,
				speed REAL NOT NULL,
				volume REAL NOT NULL,
				language TEXT NOT NULL,
				text TEXT NOT NULL,
				audio BLOB NOT NULL,
				audio_bytes INTEGER NOT NULL,
				use_count INTEGER NOT NULL DEFAULT 1,
				last_used REAL NOT NULL,
				created_at REAL NOT NULL
			);
			CREATE INDEX IF NOT EXISTS idx_speech_cache_last_used ON speech_cache(last_used);
			"""
		)
		self._conn.commit()

	def _is_cacheable_text_locked(self, text):
		if not self._policy.get("enabled", True):
			return False
		if not text:
			return False
		if not any(char.isalnum() for char in text):
			return False
		text_length = len(text)
		return self._policy["minChars"] <= text_length <= self._policy["maxChars"]

	def _make_key(self, voice, speed, volume, language, text):
		payload = {
			"voice": voice or "",
			"speed": round(float(speed), 4),
			"volume": round(float(volume), 4),
			"language": language or "",
			"text": text,
		}
		return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()

	def _prune_locked(self):
		rows = self._conn.execute(
			"SELECT cache_key, audio_bytes, use_count, last_used FROM speech_cache"
		).fetchall()
		entry_count = len(rows)
		total_bytes = sum(row[1] for row in rows)
		max_bytes = self._policy["maxBytes"]
		if total_bytes <= max_bytes:
			return
		now = time.time()
		def score(row):
			_, audio_bytes, use_count, last_used = row
			age_days = max(0.0, (now - last_used) / 86400.0)
			return (
				use_count / (1.0 + age_days),
				last_used,
				-audio_bytes,
			)
		for cache_key, audio_bytes, _, _ in sorted(rows, key=score):
			if total_bytes <= max_bytes:
				break
			self._conn.execute("DELETE FROM speech_cache WHERE cache_key = ?", (cache_key,))
			total_bytes -= audio_bytes
			entry_count -= 1
		self._conn.commit()

	def _refresh_policy_locked(self):
		try:
			mtime = os.path.getmtime(self._settings_path)
		except OSError:
			mtime = None
		if mtime == self._settings_mtime:
			return
		self._policy = resolve_cache_policy(load_cache_settings())
		self._settings_mtime = mtime
