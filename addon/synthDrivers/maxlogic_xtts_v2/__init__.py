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

def _load_engine_class():
	from ._engine import XTTSV2Engine
	return XTTSV2Engine


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
	# Seconds before the end of an utterance at which its last index is reported. See _feed_audio.
	utteranceLeadSeconds = 1.0
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
		try:
			engine_class = _load_engine_class()
		except ImportError as error:
			log.warning("Speech runtime unavailable: %s", error)
			return False
		missing = engine_class.check_runtime_requirements(PACKAGE_ROOT)
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
		if getattr(self._engine, "is_ready", True):
			self._reload_voices(log_reason="init")
		else:
			# The helper is still loading the model. Asking it for voices would freeze NVDA until it is done.
			self._load_voices_from_store()
		self._queue = queue.Queue()
		self._generation = 0
		self._terminated = False
		self._player = None
		self._voice_warm_lock = threading.Lock()
		# Utterances NVDA has sent whose "end" event the feed thread has not yet seen.
		self._unfinished = 0
		self._unfinished_lock = threading.Lock()
		self._speaking = False
		self._fed_since_sync = False
		self._events = queue.Queue(maxsize=self.prefetchQueueSize * 2)
		self._worker = threading.Thread(target=self._speech_worker, name="MaxLogicXTTSV2Speech", daemon=True)
		self._worker.start()
		self._feeder = threading.Thread(target=self._feed_worker, name="MaxLogicXTTSV2Feed", daemon=True)
		self._feeder.start()

	def _create_engine(self):
		if HelperEngineClient is not None and HelperEngineClient.should_try(PACKAGE_ROOT):
			try:
				# Speech waits for the model on the speech threads; selecting the synth does not.
				return HelperEngineClient(PACKAGE_ROOT, log, wait_until_ready=False)
			except Exception as error:
				log.warning("MaxLogic XTTS v2 helper unavailable, falling back to in-process engine: %s", error)
		return _load_engine_class()(PACKAGE_ROOT)

	def _load_voices_from_store(self):
		"""List voices the way the helper does, without the helper."""
		from ._voice_store import discover_voice_records
		roots = [PACKAGE_ROOT]
		if os.environ.get("MAXLOGIC_XTTS_V2_ASSET_ROOT"):
			roots.append(os.environ["MAXLOGIC_XTTS_V2_ASSET_ROOT"])
		records, __ = discover_voice_records(PACKAGE_ROOT, [("package", root) for root in roots])
		voices = {}
		for voice_name, record in records.items():
			language = (getattr(record, "metadata", None) or {}).get("language") or "en"
			try:
				voices[voice_name] = VoiceInfo(voice_name, record.display_name, language)
			except TypeError:
				voices[voice_name] = VoiceInfo(voice_name, record.display_name)
		self._availableVoices = voices
		if self._voice not in voices:
			self._voice = sorted(voices)[0] if voices else None
		log.info("MaxLogic XTTS v2 voices listed while the helper starts. voiceCount=%s currentVoice=%s", len(voices), self._voice)

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
		for thread in (self._worker, self._feeder):
			if thread.is_alive():
				thread.join(timeout=1.0)
		if self._player is not None:
			self._player.close()
			self._player = None
		if hasattr(self._engine, "close"):
			self._engine.close()
		super(SynthDriver, self).terminate()

	def cancel(self):
		cancelled_generation = self._generation
		self._generation += 1
		# Cancelled speech is never reported as done.
		self._speaking = False
		self._clear_pending_speech()
		if self._player is not None:
			self._player.stop()
		# Otherwise the helper finishes the abandoned text before it starts the next utterance.
		canceller = getattr(self._engine, "cancel", None)
		if canceller is not None:
			try:
				canceller(cancelled_generation)
			except Exception:
				log.debug("MaxLogic XTTS v2 engine cancel failed", exc_info=True)

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
			# This utterance will never reach the feed thread.
			with self._unfinished_lock:
				self._unfinished -= 1

	def pause(self, switch):
		if self._player is not None and hasattr(self._player, "pause"):
			self._player.pause(switch)

	def speak(self, speechSequence):
		# NVDA calls cancel() itself before speech that interrupts. Queuing lets say-all
		# send the next sentence while this one is still playing.
		tasks = self._sequence_to_tasks(speechSequence)
		with self._unfinished_lock:
			self._unfinished += 1
		self._speaking = True
		self._queue.put((self._generation, tasks))

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
		"""Synthesize NVDA's utterances in order, ahead of playback."""
		while True:
			item = self._queue.get()
			if item is None:
				self._put_event(None)
				return
			generation, tasks = item
			try:
				if generation == self._generation and not self._terminated:
					self._produce_utterance(tasks, generation)
			except Exception:
				log.exception("MaxLogic XTTS v2 speech worker failed", exc_info=True)
			finally:
				# Every utterance taken from the queue ends with this, even a cancelled one.
				self._put_event(("end", generation))

	def _put_event(self, event, generation=None):
		"""Queue an event for the feed thread. With a generation, give up once it is cancelled."""
		while True:
			if generation is not None and (generation != self._generation or self._terminated):
				return False
			try:
				self._events.put(event, timeout=0.05)
				return True
			except queue.Full:
				continue

	def _feed_worker(self):
		"""Feed synthesized audio to the player and report indexes as they are heard."""
		while True:
			try:
				event = self._events.get_nowait()
			except queue.Empty:
				self._play_out()
				event = self._events.get()
			if event is None:
				return
			kind, generation = event[0], event[1]
			if kind == "end":
				with self._unfinished_lock:
					self._unfinished -= 1
				continue
			if generation != self._generation or self._terminated:
				continue
			try:
				if kind == "audio":
					self._feed_audio(event[2], event[3], generation)
				elif kind == "index":
					self._feed_marker([event[2]], generation)
				elif kind == "error":
					log.error("MaxLogic XTTS v2 speech failed:\n%s", event[2])
			except Exception:
				log.exception("MaxLogic XTTS v2 audio feed failed", exc_info=True)

	def _play_out(self):
		"""Called when nothing is ready to feed.

		NVDA's player runs onDone callbacks only inside feed() and sync(), so
		indexes are reported only while this waits in sync().
		"""
		generation = self._generation
		if self._player is not None and self._fed_since_sync:
			self._fed_since_sync = False
			self._player.sync()
		with self._unfinished_lock:
			finished = self._unfinished == 0
		if not finished or not self._events.empty() or not self._speaking:
			return
		if generation != self._generation or self._terminated:
			return
		self._speaking = False
		if self._player is not None:
			self._player.idle()
		synthDoneSpeaking.notify(synth=self)

	def _feed(self, data, generation, onDone=None):
		if generation != self._generation or self._terminated:
			return False
		self._ensure_player()
		self._player.feed(data, onDone=onDone)
		self._fed_since_sync = True
		if generation != self._generation or self._terminated:
			# cancel() stopped the player while this feed was waiting, and this feed started it again.
			self._player.stop()
			return False
		return True

	def _feed_audio(self, audio, trailing_indexes, generation):
		if not trailing_indexes:
			self._feed(audio, generation)
			return
		# NVDA sends the next utterance only when it hears the index that ends this one.
		# Report it this long before the end, so the next one is synthesized while this one finishes.
		lead_bytes = int(self.utteranceLeadSeconds * self._engine.sample_rate) * 2
		split = max(0, len(audio) - lead_bytes)
		split -= split % 2
		if split and not self._feed(audio[:split], generation):
			return
		if self._feed_marker(trailing_indexes, generation) and split < len(audio):
			self._feed(audio[split:], generation)

	def _feed_marker(self, indexes, generation):
		"""Report indexes when the audio fed before them has been heard."""
		def reached():
			if generation == self._generation and not self._terminated:
				for index in indexes:
					synthIndexReached.notify(synth=self, index=index)

		# A millisecond of silence carries the callback, because audio already fed cannot.
		return self._feed(b"\x00\x00" * max(1, self._engine.sample_rate // 1000), generation, onDone=reached)

	def _produce_utterance(self, tasks, generation):
		"""Synthesize every piece of an utterance, in order, as events for the feed thread."""
		def put(event):
			return self._put_event(event, generation)

		sound_positions = [position for position, task in enumerate(tasks) if task["type"] in ("speak", "break")]
		last_sound = sound_positions[-1] if sound_positions else -1
		trailing_indexes = [task["index"] for task in tasks[last_sound + 1:] if task["type"] == "index"]
		trailing_sent = False
		first_text = True
		try:
			for position, task in enumerate(tasks):
				if generation != self._generation or self._terminated:
					return
				if task["type"] == "index":
					if position < last_sound and not put(("index", generation, task["index"])):
						return
					continue
				audio_items = []
				if task["type"] == "break":
					frame_count = int(self._engine.sample_rate * (max(0, task["time"]) / 1000.0))
					if frame_count:
						audio_items = [b"\x00\x00" * frame_count]
				elif task["type"] == "speak":
					speed = self._nvda_rate_to_speed(task["rate"])
					volume = max(0.0, min(1.0, task["volume"] / 100.0))
					# Only the start of an utterance waits for synthesis. Later text is ready in time.
					chunks = self._chunk_text_for_playback(task["text"], small_first_chunk=first_text)
					first_text = first_text and not chunks
					for number, chunk in enumerate(chunks):
						if generation != self._generation or self._terminated:
							return
						items = [item for item in self._synthesize_chunk_audio_items(
							chunk, speed, task["voice"], volume, task["language"], generation) if item]
						if number < len(chunks) - 1:
							for item in items:
								if not put(("audio", generation, item, ())):
									return
							items = []
						audio_items.extend(items)
				for number, item in enumerate(audio_items):
					last = position == last_sound and number == len(audio_items) - 1
					if not put(("audio", generation, item, trailing_indexes if last else ())):
						return
					trailing_sent = trailing_sent or last
			if not trailing_sent:
				# The utterance made no sound. Its indexes still have to be reported.
				for index in trailing_indexes:
					if not put(("index", generation, index)):
						return
		except Exception as error:
			if HelperRequestInterrupted is not None and isinstance(error, HelperRequestInterrupted):
				return
			put(("error", generation, traceback.format_exc()))

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

	def _synthesize_chunk_audio_items(self, text, speed, voice, volume, language, generation):
		cached_audio = self._get_cached_audio(text, voice, speed, volume, language)
		if cached_audio is not None:
			return [cached_audio]
		streamer = getattr(self._engine, "stream_synthesize_to_int16", None)
		if streamer is None:
			return [self._synthesize_chunk(text, speed, voice, volume, language, generation)]
		full_audio = bytearray()
		live_stream = os.environ.get("MAXLOGIC_XTTS_V2_LIVE_STREAM_PLAYBACK", "").strip().lower() in ("1", "true", "yes", "on")
		audio_items = []
		for audio in streamer(
			text,
			speed=speed,
			voice=voice,
			volume=volume,
			language=language,
			generation=generation,
		):
			audio_bytes = audio.tobytes() if hasattr(audio, "tobytes") else bytes(audio)
			if not audio_bytes:
				continue
			full_audio.extend(audio_bytes)
			if live_stream:
				audio_items.append(audio_bytes)
		if full_audio:
			combined_audio = bytes(full_audio)
			self._store_cached_audio(text, voice, speed, volume, language, combined_audio)
			if not live_stream:
				audio_items.append(combined_audio)
		return audio_items

	def _chunk_text_for_playback(self, text, small_first_chunk=True):
		text = self._sanitize_text_for_tts(text)
		if not text:
			return []
		chunks = []
		remaining = text
		first = small_first_chunk
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
		self._voice = value
		log.info("MaxLogic XTTS v2 voice changed to %s", value)
		# Every speech request names its voice, so the engine call only warms the voice up.
		# It waits for running synthesis, which must not freeze NVDA's settings ring.
		threading.Thread(target=self._warm_voice, args=(value,), name="MaxLogicXTTSV2VoiceWarmup", daemon=True).start()

	def _warm_voice(self, value):
		with self._voice_warm_lock:
			# Skip voices the user has already moved past.
			if value != self._voice or self._terminated:
				return
			try:
				self._engine.set_voice(value)
			except Exception:
				log.debug("MaxLogic XTTS v2 voice warmup failed for %s", value, exc_info=True)
