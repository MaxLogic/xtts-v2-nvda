import base64
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback

from _hot_text_cache import HotTextCache
from _log import configure_helper_file_logger, get_helper_log_path
from _speech_cache import SpeechCache


logging.basicConfig(level=logging.INFO, format="[maxlogic-xtts-v2-helper] %(message)s")
LOGGER = logging.getLogger("maxlogic_xtts_v2_helper")
PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
HELPER_LOG_PATH = configure_helper_file_logger(LOGGER)
HELPER_MODE = os.environ.get("MAXLOGIC_XTTS_V2_HELPER_MODE", "synth").strip().lower() or "synth"


def _send(payload):
	sys.stdout.write(json.dumps(payload) + "\n")
	sys.stdout.flush()


def _get_cached_audio(speech_cache, hot_text_cache, voice, speed, volume, language, text):
	audio_bytes = None
	cache_state = "miss"
	if speech_cache is not None:
		try:
			audio_bytes = speech_cache.get_audio(voice, speed, volume, language, text)
		except Exception as cache_error:
			LOGGER.warning("Helper speech cache read failed: %s", cache_error)
		else:
			if audio_bytes is not None:
				cache_state = "persistent"
	if audio_bytes is None:
		audio_bytes = hot_text_cache.get_audio(voice, speed, volume, language, text)
		if audio_bytes is not None:
			cache_state = "hot"
	return audio_bytes, cache_state


def _store_cached_audio(speech_cache, hot_text_cache, voice, speed, volume, language, text, audio_bytes):
	if speech_cache is not None:
		try:
			speech_cache.put_audio(voice, speed, volume, language, text, audio_bytes)
		except Exception as cache_error:
			LOGGER.warning("Helper speech cache write failed: %s", cache_error)
	hot_text_cache.put_audio(voice, speed, volume, language, text, audio_bytes)


def _send_audio_chunk(request_id, audio_bytes, index, final=False):
	_send(
		{
			"ok": True,
			"id": request_id,
			"type": "audio_chunk",
			"index": index,
			"final": bool(final),
			"audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
		}
	)


class _CancelState(object):
	"""Generations the client has cancelled. Written by the stdin reader thread."""
	def __init__(self):
		self._lock = threading.Lock()
		self._cancelled_upto = None
		self._all = False

	def cancel_upto(self, generation):
		with self._lock:
			if self._cancelled_upto is None or generation > self._cancelled_upto:
				self._cancelled_upto = generation

	def cancel_all(self):
		with self._lock:
			self._all = True

	def is_cancelled(self, generation):
		with self._lock:
			if self._all:
				return True
			return generation is not None and self._cancelled_upto is not None and generation <= self._cancelled_upto


def _pipe_lines(stream, poll_seconds=0.01):
	"""Yield the lines written to a pipe without ever waiting inside ReadFile.

	On Windows, a thread blocked in ReadFile on stdin can stop other threads
	from loading DLLs. The helper then hung while importing numpy until the
	client closed the pipe. Asking how many bytes are waiting avoids that.
	"""
	if os.name != "nt":
		yield from stream
		return
	import ctypes
	import msvcrt
	from ctypes import wintypes
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	peek = kernel32.PeekNamedPipe
	peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
	peek.restype = wintypes.BOOL
	fd = stream.fileno()
	handle = msvcrt.get_osfhandle(fd)
	if kernel32.GetFileType(wintypes.HANDLE(handle)) != 3:  # FILE_TYPE_PIPE
		# For example a file: reads do not block there.
		yield from stream
		return
	available = wintypes.DWORD()
	pending = b""
	while True:
		if not peek(handle, None, 0, None, ctypes.byref(available), None):
			break  # The client closed its end.
		if not available.value:
			time.sleep(poll_seconds)
			continue
		pending += os.read(fd, available.value)
		*lines, pending = pending.split(b"\n")
		for line in lines:
			yield line.decode("utf-8", "replace") + "\n"
	if pending:
		yield pending.decode("utf-8", "replace")


def _read_requests(stream, requests, cancel_state):
	"""Queue request lines for the main loop, but apply cancellation at once.

	The main loop is busy during synthesis, so it would only see a cancel
	request after generating audio that nobody wants any more.
	"""
	try:
		for line in _pipe_lines(stream):
			if '"cancel"' in line:
				try:
					request = json.loads(line)
				except ValueError:
					request = None
				if isinstance(request, dict) and request.get("op") == "cancel":
					if isinstance(request.get("generation"), int):
						cancel_state.cancel_upto(request["generation"])
					continue
			requests.put(line)
	finally:
		# The client is gone: stop any running synthesis and let the main loop exit.
		cancel_state.cancel_all()
		requests.put(None)


def _prewarm_engine(engine):
	if engine.current_voice is None:
		return
	start_time = time.perf_counter()
	try:
		engine.synthesize_to_int16("Warm up.", speed=1.0, voice=engine.current_voice, volume=0.0, language="en")
		LOGGER.info(
			"Helper prewarm complete. voice=%s elapsedMs=%s",
			engine.current_voice,
			round((time.perf_counter() - start_time) * 1000, 1),
		)
	except Exception as error:
		LOGGER.warning("Helper prewarm failed: %s", error)


def _prewarm_voice(engine, voice_name):
	start_time = time.perf_counter()
	try:
		engine.synthesize_to_int16("Voice warm up.", speed=1.0, voice=voice_name, volume=0.0, language="en")
		LOGGER.info(
			"Helper voice prewarm complete. voice=%s elapsedMs=%s",
			voice_name,
			round((time.perf_counter() - start_time) * 1000, 1),
		)
	except Exception as error:
		LOGGER.warning("Helper voice prewarm failed for %s: %s", voice_name, error)


def main():
	LOGGER.info("Helper starting. pid=%s packageRoot=%s logPath=%s mode=%s", os.getpid(), PACKAGE_ROOT, HELPER_LOG_PATH, HELPER_MODE)
	speech_cache = None
	engine = None
	# Drain stdin from the start, so the client never blocks on a full pipe while the model loads.
	requests = queue.Queue()
	cancel_state = _CancelState()
	threading.Thread(target=_read_requests, args=(sys.stdin, requests, cancel_state), name="HelperStdin", daemon=True).start()
	try:
		if HELPER_MODE != "cache":
			from _engine import XTTSV2Engine
			engine = XTTSV2Engine(PACKAGE_ROOT)
		try:
			speech_cache = SpeechCache(LOGGER)
		except Exception as cache_error:
			speech_cache = None
			LOGGER.warning("Helper speech cache disabled: %s", cache_error)
		hot_text_cache = HotTextCache()
		if engine is not None and os.environ.get("MAXLOGIC_XTTS_V2_SKIP_PREWARM") != "1":
			_prewarm_engine(engine)
		elif engine is not None:
			LOGGER.info("Helper startup prewarm skipped by request.")
		else:
			LOGGER.info("Helper engine startup skipped for cache-only mode.")
		providers = engine.get_status().get("providers", []) if engine is not None else []
		_send(
			{
				"ok": True,
				"type": "ready",
				"voices": engine.list_voices() if engine is not None else [],
				"current_voice": engine.current_voice if engine is not None else None,
				"providers": providers,
				"sample_rate": engine.sample_rate if engine is not None else 24000,
				"streaming": bool(engine.supports_streaming()) if engine is not None else False,
				"mode": HELPER_MODE,
			}
		)
	except Exception as error:
		_send(
			{
				"ok": False,
				"type": "ready",
				"error": str(error),
				"traceback": traceback.format_exc(),
			}
		)
		return 1

	try:
		while True:
			line = requests.get()
			if line is None:
				break
			line = line.strip()
			if not line:
				continue
			try:
				request = json.loads(line)
				request_id = request.get("id")
				op = request.get("op")
				if op == "shutdown":
					LOGGER.info("Helper shutdown requested.")
					_send({"ok": True, "id": request_id})
					return 0
				if op == "set_voice":
					if engine is None:
						raise RuntimeError("Voice operations are unavailable in cache-only helper mode")
					engine.set_voice(request["voice"])
					_prewarm_voice(engine, engine.current_voice)
					_send({"ok": True, "id": request_id, "current_voice": engine.current_voice})
					continue
				if op == "reload_voices":
					if engine is None:
						raise RuntimeError("Voice operations are unavailable in cache-only helper mode")
					current_voice = engine.reload_voices(preferred_voice=request.get("preferred_voice"))
					_send(
						{
							"ok": True,
							"id": request_id,
							"current_voice": current_voice,
							"voices": engine.list_voices(),
							"providers": engine.get_status().get("providers", []),
							"sample_rate": engine.sample_rate,
						}
					)
					continue
				if op == "clone_voice":
					if engine is None:
						raise RuntimeError("Cloning is unavailable in cache-only helper mode")
					engine.clone_voice(request["reference_paths"], request["conditioning_path"], request["options"])
					_send({"ok": True, "id": request_id})
					continue
				if op == "validate_conditioning":
					if engine is None:
						raise RuntimeError("Conditioning validation is unavailable in cache-only helper mode")
					result = engine.validate_conditioning_file(request["conditioning_path"])
					_send({"ok": True, "id": request_id, "result": result})
					continue
				if op == "get_cache_stats":
					persistent_stats = None
					if speech_cache is not None:
						try:
							persistent_stats = speech_cache.get_stats()
						except Exception as cache_error:
							LOGGER.warning("Helper speech cache stats failed: %s", cache_error)
					_send(
						{
							"ok": True,
							"id": request_id,
							"persistent": persistent_stats,
							"hot": hot_text_cache.get_stats(),
						}
					)
					continue
				if op == "clear_cache":
					if speech_cache is not None:
						try:
							speech_cache.clear()
						except Exception as cache_error:
							LOGGER.warning("Helper speech cache clear failed: %s", cache_error)
					hot_text_cache.clear()
					persistent_stats = speech_cache.get_stats() if speech_cache is not None else None
					_send({"ok": True, "id": request_id, "persistent": persistent_stats, "hot": hot_text_cache.get_stats()})
					continue
				if op == "compact_cache":
					compacted = False
					if speech_cache is not None:
						try:
							speech_cache.compact()
							compacted = True
						except Exception as cache_error:
							LOGGER.warning("Helper speech cache compact failed: %s", cache_error)
					persistent_stats = speech_cache.get_stats() if speech_cache is not None else None
					_send(
						{
							"ok": True,
							"id": request_id,
							"compacted": compacted,
							"persistent": persistent_stats,
							"hot": hot_text_cache.get_stats(),
						}
					)
					continue
				if op == "synthesize":
					if engine is None:
						raise RuntimeError("Synthesis is unavailable in cache-only helper mode")
					start_time = time.perf_counter()
					text = request["text"]
					voice = request.get("voice") or engine.current_voice
					language = request.get("language", "en-us")
					speed = request.get("speed", 1.0)
					volume = request.get("volume", 1.0)
					generation = request.get("generation")
					if cancel_state.is_cancelled(generation):
						_send({"ok": True, "id": request_id, "cancelled": True, "audio_b64": ""})
						continue
					cache_voice = engine.get_voice_cache_key(voice or engine.current_voice)
					audio_bytes, cache_state = _get_cached_audio(speech_cache, hot_text_cache, cache_voice, speed, volume, language, text)
					if audio_bytes is None:
						audio = engine.synthesize_to_int16(
							text,
							speed=speed,
							voice=voice,
							volume=volume,
							language=language,
						)
						audio_bytes = audio.tobytes()
						_store_cached_audio(speech_cache, hot_text_cache, cache_voice, speed, volume, language, text, audio_bytes)
					elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
					LOGGER.info(
						"Helper synthesize complete. chars=%s voice=%s lang=%s speed=%s volume=%s cache=%s elapsedMs=%s generation=%s",
						len(text),
						voice,
						language,
						speed,
						volume,
						cache_state,
						elapsed_ms,
						generation,
					)
					_send({"ok": True, "id": request_id, "audio_b64": base64.b64encode(audio_bytes).decode("ascii")})
					continue
				if op == "synthesize_stream":
					if engine is None:
						raise RuntimeError("Synthesis is unavailable in cache-only helper mode")
					start_time = time.perf_counter()
					text = request["text"]
					voice = request.get("voice") or engine.current_voice
					language = request.get("language", "en-us")
					speed = request.get("speed", 1.0)
					volume = request.get("volume", 1.0)
					generation = request.get("generation")
					if cancel_state.is_cancelled(generation):
						_send({"ok": True, "id": request_id, "type": "done", "chunks": 0, "cancelled": True})
						continue
					cache_voice = engine.get_voice_cache_key(voice or engine.current_voice)
					audio_bytes, cache_state = _get_cached_audio(speech_cache, hot_text_cache, cache_voice, speed, volume, language, text)
					chunk_count = 0
					first_chunk_ms = None
					cancelled = False
					if audio_bytes is not None:
						chunk_count = 1
						first_chunk_ms = round((time.perf_counter() - start_time) * 1000, 1)
						_send_audio_chunk(request_id, audio_bytes, chunk_count)
					else:
						full_audio = bytearray()
						for audio in engine.stream_synthesize_to_int16(
							text,
							speed=speed,
							voice=voice,
							volume=volume,
							language=language,
						):
							if cancel_state.is_cancelled(generation):
								cancelled = True
								break
							audio_chunk = audio.tobytes()
							if not audio_chunk:
								continue
							full_audio.extend(audio_chunk)
							chunk_count += 1
							if first_chunk_ms is None:
								first_chunk_ms = round((time.perf_counter() - start_time) * 1000, 1)
							_send_audio_chunk(request_id, audio_chunk, chunk_count)
						if cancelled:
							# Partial audio must not be cached as the whole utterance.
							cache_state = "cancelled"
						else:
							audio_bytes = bytes(full_audio)
							_store_cached_audio(speech_cache, hot_text_cache, cache_voice, speed, volume, language, text, audio_bytes)
					elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
					LOGGER.info(
						"Helper synthesize stream complete. chars=%s voice=%s lang=%s speed=%s volume=%s cache=%s chunks=%s firstChunkMs=%s elapsedMs=%s generation=%s",
						len(text),
						voice,
						language,
						speed,
						volume,
						cache_state,
						chunk_count,
						first_chunk_ms,
						elapsed_ms,
						generation,
					)
					_send({"ok": True, "id": request_id, "type": "done", "chunks": chunk_count, "elapsedMs": elapsed_ms, "cancelled": cancelled})
					continue
				if op == "synthesize_preview_stream":
					if engine is None:
						raise RuntimeError("Preview synthesis is unavailable in cache-only helper mode")
					chunks = 0
					for audio in engine.stream_synthesize_preview_to_int16(
						request["text"], conditioning_path=request["conditioning_path"],
						language=request.get("language", "en-us"), cache_key=request.get("cache_key"),
						synthesis_settings=request.get("synthesis_settings"),
					):
						chunks += 1
						_send({"ok": True, "id": request_id, "type": "audio_chunk", "audio_b64": base64.b64encode(audio.tobytes()).decode("ascii")})
					_send({"ok": True, "id": request_id, "type": "done", "chunks": chunks})
					continue

				if op == "synthesize_preview":
					if engine is None:
						raise RuntimeError("Preview synthesis is unavailable in cache-only helper mode")
					start_time = time.perf_counter()
					cache_key = request.get("cache_key")
					audio = engine.synthesize_preview_to_int16(
						request["text"],
						voice_path=request["voice_path"],
						speed=request.get("speed", 1.0),
						volume=request.get("volume", 1.0),
						language=request.get("language", "en-us"),
						cache_key=cache_key,
					)
					elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
					LOGGER.info(
						"Helper preview complete. cacheKey=%s voicePath=%s lang=%s chars=%s elapsedMs=%s",
						cache_key,
						request.get("voice_path"),
						request.get("language", "en-us"),
						len(request.get("text", "")),
						elapsed_ms,
					)
					_send({"ok": True, "id": request_id, "audio_b64": base64.b64encode(audio.tobytes()).decode("ascii")})
					continue
				_send({"ok": False, "id": request_id, "error": "Unknown operation: %s" % op})
			except Exception as error:
				context_request = request if "request" in locals() and isinstance(request, dict) else {}
				LOGGER.exception(
					"Helper request failed. op=%s voice=%s voicePath=%s lang=%s chars=%s",
					context_request.get("op"),
					context_request.get("voice"),
					context_request.get("voice_path"),
					context_request.get("language"),
					len(context_request.get("text", "")),
					exc_info=True,
				)
				_send(
					{
						"ok": False,
						"id": context_request.get("id"),
						"error": str(error),
						"traceback": traceback.format_exc(),
					}
				)
		return 0
	finally:
		if speech_cache is not None:
			speech_cache.close()
		if engine is not None:
			engine.close()


if __name__ == "__main__":
	raise SystemExit(main())
