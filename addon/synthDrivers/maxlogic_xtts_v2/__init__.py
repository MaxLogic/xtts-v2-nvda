import os
import queue
import re
import threading
import time
import traceback
import unicodedata

import addonHandler
import config
import nvwave
import synthDriverHandler
from logHandler import log
from speech.commands import BreakCommand, IndexCommand, LangChangeCommand, RateCommand, VolumeCommand
from synthDriverHandler import VoiceInfo, synthDoneSpeaking, synthIndexReached


PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))

_ENGINE_IMPORT_ERROR = None
try:
	from ._engine import XTTSV2Engine
except Exception as error:
	XTTSV2Engine = None
	_ENGINE_IMPORT_ERROR = error

try:
	from ._helper_client import HelperEngineClient, HelperRequestInterrupted
except Exception:
	HelperEngineClient = None
	HelperRequestInterrupted = None


addonHandler.initTranslation()


class SynthDriver(synthDriverHandler.SynthDriver):
	name = "maxlogic_xtts_v2"
	description = "MaxLogic XTTS v2"
	firstChunkTargetChars = 14
	firstChunkMaxChars = 24
	targetChunkChars = 90
	maxChunkChars = 130
	prefetchQueueSize = 2
	supportedSettings = (
		synthDriverHandler.SynthDriver.VoiceSetting(),
		synthDriverHandler.SynthDriver.RateSetting(),
		synthDriverHandler.SynthDriver.VolumeSetting(),
	)
	supportedCommands = {
		BreakCommand,
		IndexCommand,
		LangChangeCommand,
		RateCommand,
		VolumeCommand,
	}
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}

	@classmethod
	def check(cls):
		if HelperEngineClient is not None and HelperEngineClient.should_try(PACKAGE_ROOT):
			log.info("MaxLogic XTTS v2 check passed via helper availability.")
			return True
		if _ENGINE_IMPORT_ERROR is not None:
			log.warning("MaxLogic XTTS v2 unavailable, runtime import failed: %s", _ENGINE_IMPORT_ERROR)
			return False
		missing = XTTSV2Engine.check_runtime_requirements(PACKAGE_ROOT)
		if missing:
			log.warning("MaxLogic XTTS v2 unavailable, missing assets: %s", ", ".join(missing))
			return False
		return True

	def __init__(self):
		super(SynthDriver, self).__init__()
		self._engine = self._create_engine()
		self._rate = 50
		self._volume = 100
		self._voice = None
		self._availableVoices = {}
		self._reload_voices(log_reason="init")
		self._queue = queue.Queue()
		self._generation = 0
		self._terminated = False
		self._player = None
		self._worker = threading.Thread(target=self._speech_worker, name="MaxLogicXTTSV2Speech", daemon=True)
		self._worker.start()

	def _create_engine(self):
		if HelperEngineClient is not None and HelperEngineClient.should_try(PACKAGE_ROOT):
			try:
				return HelperEngineClient(PACKAGE_ROOT, log)
			except Exception as error:
				log.warning("MaxLogic XTTS v2 helper unavailable, falling back to in-process engine: %s", error)
		if _ENGINE_IMPORT_ERROR is not None:
			raise _ENGINE_IMPORT_ERROR
		return XTTSV2Engine(PACKAGE_ROOT)

	def _build_available_voices(self):
		voices = {}
		for voice_name in self._engine.list_voices():
			record = getattr(self._engine, "voice_records", {}).get(voice_name)
			display_name = record.display_name if record is not None else voice_name.replace("_", " ").title()
			language = "en"
			if record is not None and getattr(record, "metadata", None):
				language = record.metadata.get("language") or "en"
			try:
				voices[voice_name] = VoiceInfo(voice_name, display_name, language)
			except TypeError:
				voices[voice_name] = VoiceInfo(voice_name, display_name)
		return voices

	def _reload_voices(self, log_reason="manual", preferred_voice=None):
		reload_start = time.perf_counter()
		current_voice = self._engine.reload_voices(preferred_voice=preferred_voice or self._voice)
		self._availableVoices = self._build_available_voices()
		self._voice = current_voice
		elapsed_ms = round((time.perf_counter() - reload_start) * 1000, 1)
		status = self.getRuntimeStatus()
		log.info(
			"MaxLogic XTTS v2 voices reloaded. reason=%s currentVoice=%s voiceCount=%s elapsedMs=%s providers=%s",
			log_reason,
			self._voice,
			len(self._availableVoices),
			elapsed_ms,
			status.get("providers"),
		)
		return self._voice

	@property
	def availableVoices(self):
		return self._availableVoices

	def reloadVoiceStore(self, reason="manual", preferred_voice=None):
		return self._reload_voices(log_reason=reason, preferred_voice=preferred_voice)

	def getRuntimeStatus(self):
		if hasattr(self._engine, "get_status"):
			return self._engine.get_status()
		return {
			"mode": "unknown",
			"providers": [],
			"voiceCount": len(self._availableVoices),
			"currentVoice": self._voice,
			"pid": os.getpid(),
		}

	def _ensure_player(self):
		if self._player is None:
			output_device = None
			try:
				output_device = config.conf["audio"]["outputDevice"]
			except Exception:
				pass
			self._player = nvwave.WavePlayer(
				channels=1,
				samplesPerSec=self._engine.sample_rate,
				bitsPerSample=16,
				outputDevice=output_device,
			)

	def terminate(self):
		self._terminated = True
		self.cancel()
		self._interrupt_engine(reason="terminate", min_active_ms=0)
		self._queue.put(None)
		if self._worker.is_alive():
			self._worker.join(timeout=1.0)
		if self._player is not None:
			self._player.close()
			self._player = None
		if hasattr(self._engine, "close"):
			self._engine.close()
		super(SynthDriver, self).terminate()

	def cancel(self):
		self._generation += 1
		self._clear_pending_speech()
		if self._player is not None:
			self._player.stop()

	def _interrupt_engine(self, reason, min_active_ms=0):
		if not hasattr(self._engine, "interrupt"):
			return False
		try:
			return self._engine.interrupt(reason=reason, min_active_ms=min_active_ms)
		except Exception:
			log.debug("MaxLogic XTTS v2 helper interrupt failed", exc_info=True)
			return False

	def _clear_pending_speech(self):
		while True:
			try:
				self._queue.get_nowait()
			except queue.Empty:
				break

	def pause(self, switch):
		if self._player is not None and hasattr(self._player, "pause"):
			self._player.pause(switch)

	def speak(self, speechSequence):
		self.cancel()
		generation = self._generation
		tasks = self._sequence_to_tasks(speechSequence)
		self._queue.put((generation, tasks))

	def _sequence_to_tasks(self, speechSequence):
		tasks = []
		text_buffer = []
		current_rate = self._rate
		current_volume = self._volume
		current_lang = "en-us"
		for item in speechSequence:
			item_type = type(item)
			if item_type is str:
				text_buffer.append(item)
				continue
			if text_buffer:
				tasks.append(
					{
						"type": "speak",
						"text": "".join(text_buffer),
						"rate": current_rate,
						"volume": current_volume,
						"voice": self._voice,
						"language": current_lang,
					}
				)
				text_buffer = []
			if item_type is IndexCommand:
				tasks.append({"type": "index", "index": item.index})
			elif item_type is BreakCommand:
				tasks.append({"type": "break", "time": int(item.time)})
			elif item_type is RateCommand:
				current_rate = int(item.newValue)
			elif item_type is VolumeCommand:
				current_volume = int(item.newValue)
			elif item_type is LangChangeCommand:
				current_lang = "en-us" if item.isDefault else (item.lang or current_lang)
		if text_buffer:
			tasks.append(
				{
					"type": "speak",
					"text": "".join(text_buffer),
					"rate": current_rate,
					"volume": current_volume,
					"voice": self._voice,
					"language": current_lang,
				}
			)
		return tasks

	def _speech_worker(self):
		while True:
			item = self._queue.get()
			if item is None:
				return
			generation, tasks = item
			try:
				for task in tasks:
					if generation != self._generation or self._terminated:
						break
					task_type = task["type"]
					if task_type == "speak":
						self._speak_task(task, generation)
					elif task_type == "break":
						self._play_silence(task["time"], generation)
					elif task_type == "index":
						synthIndexReached.notify(synth=self, index=task["index"])
				if generation == self._generation and not self._terminated:
					synthDoneSpeaking.notify(synth=self)
			except Exception:
				log.exception("MaxLogic XTTS v2 speech worker failed", exc_info=True)

	def _speak_task(self, task, generation):
		speed = self._nvda_rate_to_speed(task["rate"])
		volume = max(0.0, min(1.0, task["volume"] / 100.0))
		self._ensure_player()
		chunks = self._chunk_text_for_playback(task["text"])
		if not chunks:
			return
		audio_queue = queue.Queue(maxsize=self.prefetchQueueSize)
		stop_event = threading.Event()
		producer = threading.Thread(
			target=self._produce_chunk_audio,
			args=(audio_queue, stop_event, chunks, speed, task["voice"], volume, task["language"], generation),
			name="MaxLogicXTTSV2Prefetch",
			daemon=True,
		)
		producer.start()
		try:
			while True:
				if generation != self._generation or self._terminated:
					stop_event.set()
					return
				try:
					item = audio_queue.get(timeout=0.05)
				except queue.Empty:
					continue
				if item is None:
					break
				if isinstance(item, dict) and "error" in item:
					raise RuntimeError(item["error"])
				__, audio_bytes = item
				self._player.feed(audio_bytes)
		finally:
			stop_event.set()
		self._player.idle()

	def _produce_chunk_audio(self, audio_queue, stop_event, chunks, speed, voice, volume, language, generation):
		try:
			for chunk in chunks:
				if stop_event.is_set():
					return
				audio = self._synthesize_chunk(chunk, speed, voice, volume, language, generation)
				while not stop_event.is_set():
					try:
						audio_queue.put((chunk, audio), timeout=0.05)
						break
					except queue.Full:
						continue
		except Exception as error:
			if HelperRequestInterrupted is not None and isinstance(error, HelperRequestInterrupted):
				return
			error_item = {"error": traceback.format_exc()}
			while not stop_event.is_set():
				try:
					audio_queue.put(error_item, timeout=0.05)
					break
				except queue.Full:
					continue
		finally:
			while not stop_event.is_set():
				try:
					audio_queue.put(None, timeout=0.05)
					break
				except queue.Full:
					continue

	def _synthesize_chunk(self, text, speed, voice, volume, language, generation):
		cached_audio = self._get_cached_audio(text, voice, speed, volume, language)
		if cached_audio is not None:
			return cached_audio
		audio = self._engine.synthesize_to_int16(
			text,
			speed=speed,
			voice=voice,
			volume=volume,
			language=language,
			generation=generation,
		)
		audio_bytes = audio.tobytes() if hasattr(audio, "tobytes") else bytes(audio)
		self._store_cached_audio(text, voice, speed, volume, language, audio_bytes)
		return audio_bytes

	def _chunk_text_for_playback(self, text):
		text = self._sanitize_text_for_tts(text)
		if not text:
			return []
		chunks = []
		remaining = text
		first = True
		while remaining:
			target = self.firstChunkTargetChars if first else self.targetChunkChars
			max_chars = self.firstChunkMaxChars if first else self.maxChunkChars
			chunk, remaining = self._split_text_once(
				remaining,
				target_chars=target,
				max_chars=max_chars,
				min_chars=8 if first else 48,
			)
			chunks.append(chunk)
			first = False
		return chunks

	def _sanitize_text_for_tts(self, text):
		text = text or ""
		sanitized = []
		for char in text:
			category = unicodedata.category(char)
			if category in ("Cc", "Cf", "Co", "Cs"):
				if char.isspace():
					sanitized.append(" ")
				continue
			sanitized.append(char)
		return re.sub(r"\s+", " ", "".join(sanitized)).strip()

	def _split_text_once(self, text, target_chars, max_chars, min_chars=48):
		if len(text) <= max_chars:
			return text, ""
		search_end = min(len(text), max_chars)
		boundary = self._find_preferred_boundary(text, target_chars, search_end, min_chars)
		if boundary is None:
			boundary = search_end
		chunk = text[:boundary].strip()
		remaining = text[boundary:].strip()
		if not chunk:
			chunk = text[:search_end].strip()
			remaining = text[search_end:].strip()
		return chunk, remaining

	def _find_preferred_boundary(self, text, target_chars, search_end, min_chars):
		candidates = []
		target_window_end = min(search_end, max(target_chars + 48, min_chars))
		for index in range(min_chars, search_end):
			char = text[index]
			next_char = text[index + 1] if index + 1 < len(text) else ""
			prev_char = text[index - 1] if index > 0 else ""
			boundary = None
			priority = None
			if char in ".!?" and (not next_char or next_char.isspace() or next_char in "\"')]}"):
				boundary = index + 1
				priority = 0
			elif char in ",;:" and (not next_char or next_char.isspace()):
				boundary = index + 1
				priority = 1
			elif char in "-\u2013\u2014" and prev_char.isspace() and (not next_char or next_char.isspace()):
				boundary = index + 1
				priority = 2
			elif char.isspace():
				boundary = index
				priority = 3
			if boundary is None or boundary < min_chars or boundary > search_end:
				continue
			if boundary <= target_window_end:
				distance = abs(target_chars - boundary)
			else:
				distance = 1000 + (boundary - target_window_end)
			candidates.append((priority, distance, -boundary, boundary))
		if not candidates:
			return None
		candidates.sort()
		return candidates[0][3]

	def _get_cached_audio(self, text, voice, speed, volume, language):
		return None

	def _store_cached_audio(self, text, voice, speed, volume, language, audio_bytes):
		return

	def _play_silence(self, duration_ms, generation):
		if duration_ms <= 0 or generation != self._generation or self._terminated:
			return
		self._ensure_player()
		frame_count = int(self._engine.sample_rate * (duration_ms / 1000.0))
		if frame_count <= 0:
			return
		self._player.feed((b"\x00\x00" * frame_count))
		self._player.idle()

	def _nvda_rate_to_speed(self, rate):
		rate = max(0, min(100, int(rate)))
		if rate <= 50:
			return max(0.7, min(1.0, 0.7 + (rate / 50.0) * 0.3))
		return max(1.0, min(1.4, 1.0 + ((rate - 50) / 50.0) * 0.4))

	def _get_rate(self):
		return self._rate

	def _set_rate(self, value):
		self._rate = max(0, min(100, int(value)))

	def _get_volume(self):
		return self._volume

	def _set_volume(self, value):
		self._volume = max(0, min(100, int(value)))

	def _get_voice(self):
		return self._voice

	def _set_voice(self, value):
		if value not in self._availableVoices:
			raise KeyError("Unknown voice: %s" % value)
		self._engine.set_voice(value)
		self._voice = value
		log.info("MaxLogic XTTS v2 voice changed to %s", value)
