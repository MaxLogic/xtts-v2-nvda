import base64
import json
import logging
import os
import sys
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
		for line in sys.stdin:
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
					cache_voice = engine.get_voice_cache_key(voice or engine.current_voice)
					audio_bytes, cache_state = _get_cached_audio(speech_cache, hot_text_cache, cache_voice, speed, volume, language, text)
					chunk_count = 0
					first_chunk_ms = None
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
							audio_chunk = audio.tobytes()
							if not audio_chunk:
								continue
							full_audio.extend(audio_chunk)
							chunk_count += 1
							if first_chunk_ms is None:
								first_chunk_ms = round((time.perf_counter() - start_time) * 1000, 1)
							_send_audio_chunk(request_id, audio_chunk, chunk_count)
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
					_send({"ok": True, "id": request_id, "type": "done", "chunks": chunk_count, "elapsedMs": elapsed_ms})
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
