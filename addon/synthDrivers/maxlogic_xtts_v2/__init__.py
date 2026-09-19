import contextlib
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


from ._loading_sounds import LoadingAnnouncer, play_loading_sound


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

# Characters XTTS accepts per request before it warns and may cut the audio short,
# copied from TTS.tts.layers.xtts.tokenizer.VoiceBpeTokenizer.char_limits.
XTTS_CHAR_LIMITS = {
	"en": 250, "de": 253, "fr": 273, "es": 239, "it": 213, "pt": 203, "pl": 224, "zh": 82,
	"ar": 166, "cs": 186, "ru": 182, "nl": 251, "tr": 226, "ja": 71, "hu": 224, "ko": 95,
}
# The end of a sentence: . ! ? or an ellipsis before a space, or CJK end punctuation.
SENTENCE_END = re.compile(r"[.!?\u2026]+[\"')\]}]*(?=\s|$)|[\u3002\uff01\uff1f]+")


class SynthDriver(synthDriverHandler.SynthDriver):
	name = "maxlogic_xtts_v2"
	description = "MaxLogic XTTS v2"
	# Chunks stay this far under XTTS's text limit for the language. See XTTS_CHAR_LIMITS.
	chunkLimitRatio = 0.8
	prefetchQueueSize = 2
	# Seconds before the end of an utterance at which its last index is reported. See _feed_audio.
	utteranceLeadSeconds = 1.0
	# Audio is fed in slices this long, so the played position is known this precisely.
	feedSliceSeconds = 0.05
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
			self._announce_loading()
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
		# Samples fed and played in the generation being fed, and the utterance end waiting to be reported.
		self._counted_generation = None
		self._fed_samples = 0
		self._played_samples = 0
		self._pending_end = None
		self._pending_end_lock = threading.Lock()
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

	def _announce_loading(self):
		"""Announce the loading now and again every few seconds, and the ready state once the model has loaded."""
		# Looked up on each call, so tests can replace play_loading_sound.
		self._loading_announcer = LoadingAnnouncer(play=lambda kind, **kwargs: play_loading_sound(kind, **kwargs), logger=log)
		self._loading_announcer.start()
		call_when_ready = getattr(self._engine, "call_when_ready", None)
		if call_when_ready is not None and not call_when_ready(self._announce_ready):
			# It became ready in the meantime.
			self._loading_announcer.finish(wait=False)

	def _announce_ready(self):
		# This can run before __init__ has set _terminated.
		if getattr(self, "_terminated", False):
			self._loading_announcer.stop()
			return
		# Waiting holds back speech until the sound has finished, so the two do not overlap.
		self._loading_announcer.finish()

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
		announcer = getattr(self, "_loading_announcer", None)
		if announcer is not None:
			announcer.stop(wait=False)
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
					self._feed_audio(event[2], generation)
				elif kind == "utterance_end":
					self._report_before_end(event[2], generation)
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
		# All audio has been heard, so an utterance end not yet reported is due.
		self._report_pending_end(force=True)
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

	def _feed_audio(self, audio, generation):
		if generation != self._counted_generation:
			self._counted_generation = generation
			self._fed_samples = 0
			self._played_samples = 0
			with self._pending_end_lock:
				self._pending_end = None
		slice_bytes = max(1, int(self.feedSliceSeconds * self._engine.sample_rate)) * 2
		for start in range(0, len(audio), slice_bytes):
			part = audio[start:start + slice_bytes]
			self._fed_samples += len(part) // 2
			played = self._fed_samples

			def reached(played=played):
				if generation == self._counted_generation:
					self._played_samples = max(self._played_samples, played)
					self._report_pending_end()

			if not self._feed(part, generation, onDone=reached):
				return

	def _report_before_end(self, indexes, generation):
		"""Report the indexes that end an utterance utteranceLeadSeconds before its audio ends.

		NVDA sends the next utterance only when it hears the index that ends this one.
		Reporting it early lets the next one be synthesized while this one finishes.
		"""
		if generation != self._counted_generation:
			# The utterance made no sound.
			self._counted_generation = generation
			self._fed_samples = 0
			self._played_samples = 0
		lead_samples = int(self.utteranceLeadSeconds * self._engine.sample_rate)
		with self._pending_end_lock:
			self._pending_end = (generation, self._fed_samples - lead_samples, indexes)
		self._report_pending_end()

	def _report_pending_end(self, force=False):
		with self._pending_end_lock:
			pending = self._pending_end
			if pending is None or (not force and self._played_samples < pending[1]):
				return
			self._pending_end = None
		generation, __, indexes = pending
		if generation == self._generation and not self._terminated:
			for index in indexes:
				synthIndexReached.notify(synth=self, index=index)

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
		try:
			for position, task in enumerate(tasks):
				if generation != self._generation or self._terminated:
					return
				if task["type"] == "index":
					if position < last_sound and not put(("index", generation, task["index"])):
						return
					continue
				if task["type"] == "break":
					frame_count = int(self._engine.sample_rate * (max(0, task["time"]) / 1000.0))
					if frame_count and not put(("audio", generation, b"\x00\x00" * frame_count)):
						return
				elif task["type"] == "speak":
					speed = self._nvda_rate_to_speed(task["rate"])
					volume = max(0.0, min(1.0, task["volume"] / 100.0))
					for chunk in self._chunk_text_for_playback(task["text"], task["language"]):
						if generation != self._generation or self._terminated:
							return
						# Play each piece as it arrives: a whole chunk takes seconds to synthesize.
						live = self._live_stream_playback()
						whole = bytearray()
						pieces = self._synthesize_chunk_pieces(chunk, speed, task["voice"], volume, task["language"], generation)
						with contextlib.closing(pieces):
							for piece in pieces:
								if not live:
									whole.extend(piece)
								elif not put(("audio", generation, piece)):
									return
						if whole and not put(("audio", generation, bytes(whole))):
							return
			if trailing_indexes:
				put(("utterance_end", generation, trailing_indexes))
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

	def _live_stream_playback(self):
		"""Streamed pieces play as they arrive unless MAXLOGIC_XTTS_V2_LIVE_STREAM_PLAYBACK turns it off."""
		return os.environ.get("MAXLOGIC_XTTS_V2_LIVE_STREAM_PLAYBACK", "").strip().lower() not in ("0", "false", "no", "off")

	def _synthesize_chunk_pieces(self, text, speed, voice, volume, language, generation):
		"""Yield the chunk's audio in the pieces the helper streams it in."""
		cached_audio = self._get_cached_audio(text, voice, speed, volume, language)
		if cached_audio is not None:
			yield cached_audio
			return
		streamer = getattr(self._engine, "stream_synthesize_to_int16", None)
		if streamer is None:
			yield self._synthesize_chunk(text, speed, voice, volume, language, generation)
			return
		full_audio = bytearray()
		# Closing the stream early, on cancel, releases the helper client for the next request.
		stream = streamer(text, speed=speed, voice=voice, volume=volume, language=language, generation=generation)
		with contextlib.closing(stream):
			for audio in stream:
				audio_bytes = audio.tobytes() if hasattr(audio, "tobytes") else bytes(audio)
				if not audio_bytes:
					continue
				full_audio.extend(audio_bytes)
				yield audio_bytes
		if full_audio:
			self._store_cached_audio(text, voice, speed, volume, language, bytes(full_audio))

	def _chunk_limit(self, language):
		key = (language or "en").strip().lower().replace("_", "-").split("-", 1)[0]
		return int(XTTS_CHAR_LIMITS.get(key, 250) * self.chunkLimitRatio)

	def _chunk_text_for_playback(self, text, language=None):
		"""Split text into as few requests as possible, at sentence ends, within XTTS's text limit."""
		text = self._sanitize_text_for_tts(text)
		if not text:
			return []
		limit = self._chunk_limit(language)
		chunks = []
		current = ""
		for sentence in self._sentences(text):
			candidate = current + sentence
			if len(candidate.strip()) <= limit:
				current = candidate
				continue
			if current.strip():
				chunks.append(current.strip())
			current = sentence
			# A sentence over the limit is split at its best clause boundary.
			while len(current.strip()) > limit:
				head, rest = self._split_text_once(current.strip(), target_chars=limit, max_chars=limit, min_chars=min(48, limit // 2))
				chunks.append(head)
				current = rest
		if current.strip():
			chunks.append(current.strip())
		return chunks

	def _sentences(self, text):
		"""Yield the sentences of text, each with the whitespace in front of it."""
		start = 0
		for match in SENTENCE_END.finditer(text):
			yield text[start:match.end()]
			start = match.end()
		if start < len(text):
			yield text[start:]

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
