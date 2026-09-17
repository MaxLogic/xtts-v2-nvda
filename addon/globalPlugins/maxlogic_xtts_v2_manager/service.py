# coding: utf-8

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import hashlib
import wave

import addonHandler
import config
import nvwave
import synthDriverHandler
from logHandler import log
import wx


addonHandler.initTranslation()

_GLOBAL_PLUGIN_ROOT = os.path.abspath(os.path.dirname(__file__))
_SYNTH_ROOT = os.path.abspath(os.path.join(_GLOBAL_PLUGIN_ROOT, "..", "..", "synthDrivers", "maxlogic_xtts_v2"))

from synthDrivers.maxlogic_xtts_v2._catalog import (
	download_catalog_voice,
	download_catalog_voice_to_temp,
	get_catalog_entries,
)
from synthDrivers.maxlogic_xtts_v2._huggingface import search_huggingface_entries
from synthDrivers.maxlogic_xtts_v2._cache_settings import (
	CACHE_MODE_CUSTOM,
	CACHE_MODE_SHORT_MEDIUM,
	CACHE_MODE_SHORT_UI,
	load_cache_settings,
	resolve_cache_policy,
	save_cache_settings,
)
from synthDrivers.maxlogic_xtts_v2._helper_client import HelperEngineClient
from synthDrivers.maxlogic_xtts_v2._paths import get_cache_dir, get_temp_dir
from synthDrivers.maxlogic_xtts_v2._voice_store import (
	DuplicateVoiceError,
	VoiceStoreError,
	discover_voice_records,
	install_voice_files,
	list_user_voice_records,
	normalize_voice_id,
	remove_user_voice,
)


_preview_lock = threading.Lock()
_preview_helper_lock = threading.Lock()
_preview_generation = 0
_preview_player = None
_preview_player_format = None
_preview_helper = None
_preview_helper_thread = None
_sample_text_cache = None
_PREVIEW_WAV_CACHE_VERSION = 1
_PREVIEW_WAV_CACHE_DIR_NAME = "preview-wav"

_PREVIEW_LANGUAGE_LABELS = {
	"ar": _("Arabic"),
	"cs": _("Czech"),
	"de": _("German"),
	"en": _("English"),
	"en-gb": _("English (United Kingdom)"),
	"en-us": _("English (United States)"),
	"es": _("Spanish"),
	"fr": _("French"),
	"hu": _("Hungarian"),
	"it": _("Italian"),
	"ja": _("Japanese"),
	"ko": _("Korean"),
	"nl": _("Dutch"),
	"pl": _("Polish"),
	"pt": _("Portuguese"),
	"ru": _("Russian"),
	"tr": _("Turkish"),
	"zh-cn": _("Chinese (Simplified)"),
}


CACHE_MODE_OPTIONS = [
	(CACHE_MODE_SHORT_UI, _("Short UI speech only (Recommended)")),
	(CACHE_MODE_SHORT_MEDIUM, _("Short and medium speech")),
	(CACHE_MODE_CUSTOM, _("Custom")),
]


def list_installed_user_voices():
	return list_user_voice_records()


def _package_root():
	return _SYNTH_ROOT


def _repo_root():
	return os.path.abspath(os.path.join(_GLOBAL_PLUGIN_ROOT, "..", ".."))


def _bootstrap_script_path():
	return os.path.join(_repo_root(), "bootstrap-helper-env.ps1")


def _sample_text_path():
	return os.path.join(os.path.dirname(__file__), "sample_texts.json")


def _load_sample_texts():
	global _sample_text_cache
	if _sample_text_cache is None:
		with open(_sample_text_path(), "r", encoding="utf-8") as handle:
			_sample_text_cache = json.load(handle)
	return _sample_text_cache


def clone_voice(reference_paths, name, language, options, synthesis_settings=None):
	from synthDrivers.maxlogic_xtts_v2._cloning import create_voice
	def clone(paths, target, settings):
		_get_preview_helper(skip_prewarm=True).clone_voice(paths, target, settings)
	return create_voice(reference_paths, name, language, options, clone, synthesis_settings)


def clone_voice_draft(reference_paths, language, options, synthesis_settings=None):
	from synthDrivers.maxlogic_xtts_v2._cloning import create_draft
	def clone(paths, target, settings):
		_get_preview_helper(skip_prewarm=True).clone_voice(paths, target, settings)
	return create_draft(reference_paths, language, options, clone, synthesis_settings)


def save_voice_draft(record, name, overwrite=False):
	from synthDrivers.maxlogic_xtts_v2._cloning import save_draft
	return save_draft(record, name, overwrite=overwrite)


def discard_voice_draft(record):
	from synthDrivers.maxlogic_xtts_v2._cloning import discard_draft
	return discard_draft(record)


def get_sample_text(language):
	payload = _load_sample_texts()
	key = (language or "").strip().lower()
	if key and key in payload:
		return payload[key]
	if "-" in key:
		base_key = key.split("-", 1)[0]
		if base_key in payload:
			return payload[base_key]
	return payload["default"]


def get_preview_language_options():
	payload = _load_sample_texts()
	keys = sorted(key for key in payload.keys() if key != "default")
	return [
		(key, _PREVIEW_LANGUAGE_LABELS.get(key, key))
		for key in keys
	]


def _preview_wav_cache_dir():
	path = os.path.join(get_cache_dir(create=True), _PREVIEW_WAV_CACHE_DIR_NAME)
	os.makedirs(path, exist_ok=True)
	return path


def _fingerprint_file(path):
	if not path or not os.path.isfile(path):
		return None
	stat = os.stat(path)
	return {
		"name": os.path.basename(path),
		"size": int(stat.st_size),
		"mtimeNs": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1000000000))),
	}


def _preview_cache_path(cache_payload):
	cache_key = hashlib.sha256(
		json.dumps(cache_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
	).hexdigest()
	return os.path.join(_preview_wav_cache_dir(), "%s.wav" % cache_key)


def _read_preview_wav_cache(cache_payload):
	cache_path = _preview_cache_path(cache_payload)
	if not os.path.isfile(cache_path):
		return None
	try:
		with wave.open(cache_path, "rb") as handle:
			if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
				raise ValueError("Unsupported preview cache format")
			return {
				"path": cache_path,
				"sampleRate": int(handle.getframerate()),
				"audioBytes": handle.readframes(handle.getnframes()),
			}
	except Exception:
		log.warning("MaxLogic XTTS v2 preview WAV cache unreadable, removing: %s", cache_path, exc_info=True)
		try:
			os.remove(cache_path)
		except Exception:
			pass
		return None


def _write_preview_wav_cache(cache_payload, sample_rate, audio_bytes):
	cache_path = _preview_cache_path(cache_payload)
	temp_fd, temp_path = tempfile.mkstemp(prefix="maxlogic-xtts-v2-preview-", suffix=".wav", dir=_preview_wav_cache_dir())
	os.close(temp_fd)
	try:
		with wave.open(temp_path, "wb") as handle:
			handle.setnchannels(1)
			handle.setsampwidth(2)
			handle.setframerate(int(sample_rate))
			handle.writeframes(audio_bytes)
		os.replace(temp_path, cache_path)
	except Exception:
		try:
			os.remove(temp_path)
		except Exception:
			pass
		raise


def _begin_preview():
	global _preview_generation
	with _preview_lock:
		_preview_generation += 1
		generation = _preview_generation
		if _preview_player is not None:
			_preview_player.stop()
		return generation


def _get_preview_player(sample_rate, channels=1, bits_per_sample=16):
	global _preview_player
	global _preview_player_format
	player_format = (int(sample_rate), int(channels), int(bits_per_sample))
	if _preview_player is not None and _preview_player_format != player_format:
		_preview_player.close()
		_preview_player = None
		_preview_player_format = None
	if _preview_player is None:
		output_device = None
		try:
			output_device = config.conf["audio"]["outputDevice"]
		except Exception:
			pass
		_preview_player = nvwave.WavePlayer(
			channels=int(channels),
			samplesPerSec=sample_rate,
			bitsPerSample=int(bits_per_sample),
			outputDevice=output_device,
		)
		_preview_player_format = player_format
	return _preview_player


def _play_preview_audio(audio_bytes, sample_rate, generation, on_playback_started=None):
	with _preview_lock:
		if generation != _preview_generation:
			return "superseded"
		player = _get_preview_player(sample_rate)
		player.stop()
		player.feed(audio_bytes)
	if on_playback_started is not None:
		wx.CallAfter(on_playback_started)
	player.idle()
	with _preview_lock:
		if generation != _preview_generation:
			return "superseded"
	return "completed"


def _play_preview_stream(chunks, sample_rate, generation, cache_payload, on_playback_started=None):
	"""Play each generated chunk, but cache only the complete utterance.

	A stopped preview is still drained so the helper protocol stays synchronized
	and the completed WAV is available for the next audition.
	"""
	parts = []
	player = None
	started = False
	for chunk in chunks:
		audio = bytes(chunk)
		if not audio:
			continue
		parts.append(audio)
		with _preview_lock:
			if generation != _preview_generation:
				continue
			player = _get_preview_player(sample_rate)
			if not started:
				player.stop()
			player.feed(audio)
		if not started:
			started = True
			if on_playback_started is not None:
				wx.CallAfter(on_playback_started)
	if not parts:
		raise RuntimeError("The speech engine returned an empty preview.")
	try:
		_write_preview_wav_cache(cache_payload, sample_rate, b"".join(parts))
	except Exception:
		log.warning("MaxLogic XTTS v2 preview WAV cache write failed", exc_info=True)
	if player is not None and generation == _preview_generation:
		player.idle()
	return "completed" if generation == _preview_generation else "superseded"


def stop_preview():
	global _preview_generation
	with _preview_lock:
		_preview_generation += 1
		if _preview_player is not None:
			_preview_player.stop()
			return True
	return False


def close_preview_player():
	global _preview_player
	global _preview_player_format
	with _preview_lock:
		if _preview_player is not None:
			_preview_player.close()
			_preview_player = None
			_preview_player_format = None


def _get_preview_helper(skip_prewarm=True):
	global _preview_helper
	with _preview_helper_lock:
		if _preview_helper is not None:
			return _preview_helper
		_preview_helper = HelperEngineClient(_package_root(), log, skip_prewarm=skip_prewarm)
		return _preview_helper


def close_preview_helper():
	global _preview_helper
	with _preview_helper_lock:
		helper = _preview_helper
		_preview_helper = None
	if helper is not None:
		helper.close()


def _invalidate_preview_helper(reason):
	close_preview_helper()
	log.info("MaxLogic XTTS v2 preview helper invalidated. reason=%s", reason)


def prepare_preview_runtime_async():
	global _preview_helper_thread
	def _worker():
		global _preview_helper_thread
		try:
			_get_preview_helper(skip_prewarm=False)
		except Exception as error:
			log.warning("MaxLogic XTTS v2 preview warmup failed: %s", error)
			close_preview_helper()
		finally:
			with _preview_helper_lock:
				_preview_helper_thread = None

	# Another page can request warmup while helper startup owns this lock.
	# Never make the NVDA GUI thread wait for the external process.
	if not _preview_helper_lock.acquire(blocking=False):
		return False
	try:
		if _preview_helper is not None:
			return False
		if _preview_helper_thread is not None and _preview_helper_thread.is_alive():
			return False
		thread = threading.Thread(
			target=_worker,
			name="MaxLogicXTTSV2PreviewWarmup",
			daemon=True,
		)
		_preview_helper_thread = thread
		thread.start()
	finally:
		_preview_helper_lock.release()
	return True


def list_voice_inventory():
	package_root = _package_root()
	override_root = os.environ.get("MAXLOGIC_XTTS_V2_ASSET_ROOT")
	fallback_roots = [("package", package_root)]
	if override_root:
		fallback_roots.append(("override", override_root))
	records, __ = discover_voice_records(package_root, fallback_roots)
	user_records = []
	builtin_records = []
	for record in records.values():
		if record.source == "user":
			user_records.append(record)
		else:
			builtin_records.append(record)
	user_records.sort(key=lambda record: record.display_name.lower())
	builtin_records.sort(key=lambda record: record.display_name.lower())
	return {
		"user": user_records,
		"builtin": builtin_records,
	}


def get_setup_status():
	helper_python = os.path.join(_repo_root(), ".helper-venv", "Scripts", "python.exe")
	inventory = list_voice_inventory()
	has_user_profiles = bool(inventory["user"])
	has_packaged_profiles = bool(inventory["builtin"])
	has_any_profiles = bool(inventory["user"] or inventory["builtin"])
	helper_ready = os.path.isfile(helper_python)
	return {
		"helperPython": helper_python,
		"helperReady": helper_ready,
		"hasUserProfiles": has_user_profiles,
		"hasPackagedProfiles": has_packaged_profiles,
		"hasAnyProfiles": has_any_profiles,
		"needsSetup": not (helper_ready and has_any_profiles),
		"message": (
			_("XTTS is ready.")
			if helper_ready and has_any_profiles
			else _("XTTS still needs setup. Use the setup button to install or repair the helper runtime.")
		),
	}


def _get_active_maxlogic_synth():
	try:
		synth = synthDriverHandler.getSynth()
	except Exception:
		return None
	if synth is None or getattr(synth, "name", None) != "maxlogic_xtts_v2":
		return None
	return synth


def run_runtime_setup():
	script = _bootstrap_script_path()
	if not os.path.isfile(script):
		raise RuntimeError("Bootstrap script not found: %s" % script)
	command = [
		"powershell.exe",
		"-NoProfile",
		"-ExecutionPolicy",
		"Bypass",
		"-File",
		script,
	]
	result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
	stdout = (result.stdout or "").strip()
	stderr = (result.stderr or "").strip()
	payload = {
		"ok": result.returncode == 0,
		"returnCode": result.returncode,
		"stdout": stdout,
		"stderr": stderr,
	}
	if stdout:
		try:
			last_json = stdout.splitlines()[-1]
			payload.update(json.loads(last_json))
		except Exception:
			pass
	if not payload["ok"] and not payload.get("stderr"):
		payload["stderr"] = "Bootstrap command failed with exit code %s" % result.returncode
	return payload


def _helper_python_path():
	return HelperEngineClient._repo_helper_python(_package_root())


def _validate_conditioning_file(conditioning_path):
	helper = None
	try:
		helper = _get_preview_helper(skip_prewarm=True)
		return helper.validate_conditioning_file(conditioning_path)
	except Exception as error:
		close_preview_helper()
		raise VoiceStoreError("XTTS conditioning validation failed: %s" % error)


def _audio_tool_script_path():
	return os.path.join(_package_root(), "_audio_tools.py")


def _run_audio_tool(arguments):
	helper_python = _helper_python_path()
	if not os.path.isfile(helper_python):
		raise RuntimeError("XTTS helper runtime is not installed yet.")
	script_path = _audio_tool_script_path()
	if not os.path.isfile(script_path):
		raise RuntimeError("Audio tool script not found: %s" % script_path)
	command = [helper_python, script_path] + list(arguments)
	result = subprocess.run(
		command,
		capture_output=True,
		text=True,
		encoding="utf-8",
		errors="replace",
		env=dict(os.environ, COQUI_TOS_AGREED="1"),
		creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
	)
	stdout = (result.stdout or "").strip()
	stderr = (result.stderr or "").strip()
	payload = None
	if stdout:
		for line in reversed(stdout.splitlines()):
			line = line.strip()
			if not line:
				continue
			try:
				payload = json.loads(line)
				break
			except Exception:
				continue
	if payload is None:
		if result.returncode != 0:
			raise RuntimeError(stderr or stdout or "Audio helper command failed")
		raise RuntimeError("Audio helper command returned no JSON payload")
	if not payload.get("ok"):
		raise RuntimeError(payload.get("error") or stderr or "Audio helper command failed")
	return payload.get("result") or {}


def probe_audio_source(source_path):
	return _run_audio_tool(["probe", "--path", source_path])


def create_audio_working_copy(source_path):
	if not os.path.isfile(source_path):
		raise RuntimeError("Source audio file not found: %s" % source_path)
	temp_dir = tempfile.mkdtemp(prefix="source-edit-", dir=get_temp_dir(create=True))
	target_path = os.path.join(temp_dir, "working-copy.wav")
	try:
		_run_audio_tool(
			[
				"convert-to-wav",
				"--path",
				source_path,
				"--out",
				target_path,
			]
		)
	except Exception:
		try:
			shutil.rmtree(temp_dir)
		except Exception:
			pass
		raise
	return {
		"originalPath": source_path,
		"workingPath": target_path,
		"cleanupDir": temp_dir,
		"workingFormat": "WAV",
	}


def delete_audio_working_copy(payload):
	if not payload:
		return
	cleanup_dir = payload.get("cleanupDir")
	working_path = payload.get("workingPath")
	if cleanup_dir and os.path.isdir(cleanup_dir):
		shutil.rmtree(cleanup_dir, ignore_errors=True)
	elif working_path and os.path.isfile(working_path):
		try:
			os.remove(working_path)
		except Exception:
			pass


def delete_audio_source_segment(source_path, start_ms, end_ms):
	return _run_audio_tool(
		[
			"delete-snippet",
			"--path",
			source_path,
			"--start-ms",
			str(float(start_ms)),
			"--end-ms",
			str(float(end_ms)),
		]
	)


def save_audio_working_copy(payload, target_path):
	if not payload or not payload.get("workingPath"):
		raise RuntimeError("No temporary working audio is available.")
	source_path = payload["workingPath"]
	if not os.path.isfile(source_path):
		raise RuntimeError("Temporary working audio file not found.")
	target_dir = os.path.dirname(target_path)
	if target_dir:
		os.makedirs(target_dir, exist_ok=True)
	shutil.copy2(source_path, target_path)
	return probe_audio_source(target_path)


def export_audio_source_segment(source_path, target_path, start_ms, end_ms):
	target_dir = os.path.dirname(target_path)
	if target_dir:
		os.makedirs(target_dir, exist_ok=True)
	return _run_audio_tool(
		[
			"copy-segment",
			"--path",
			source_path,
			"--out",
			target_path,
			"--start-ms",
			str(float(start_ms)),
			"--end-ms",
			str(float(end_ms)),
		]
	)


def _read_pcm16_wav(path):
	with wave.open(path, "rb") as handle:
		if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
			raise RuntimeError("Audio helper returned an unsupported playback WAV format.")
		return {
			"sampleRate": int(handle.getframerate()),
			"audioBytes": handle.readframes(handle.getnframes()),
			"durationMs": int(round((float(handle.getnframes()) / float(handle.getframerate())) * 1000.0)),
		}


def render_audio_source_segment(source_path, start_ms, end_ms):
	temp_dir = tempfile.mkdtemp(dir=get_temp_dir(create=True))
	temp_wav_path = os.path.join(temp_dir, "source-preview.wav")
	try:
		_run_audio_tool(
			[
				"extract",
				"--path",
				source_path,
				"--out",
				temp_wav_path,
				"--start-ms",
				str(float(start_ms)),
				"--end-ms",
				str(float(end_ms)),
			]
		)
		return _read_pcm16_wav(temp_wav_path)
	finally:
		if os.path.isfile(temp_wav_path):
			os.remove(temp_wav_path)
		if os.path.isdir(temp_dir):
			try:
				os.rmdir(temp_dir)
			except OSError:
				pass


def _can_stream_source_wav(source_path):
	if os.path.splitext(source_path)[1].lower() != ".wav":
		return False
	try:
		with wave.open(source_path, "rb") as handle:
			compression = handle.getcomptype()
			sample_width = int(handle.getsampwidth())
			channels = int(handle.getnchannels())
			return compression == "NONE" and sample_width in (1, 2) and channels in (1, 2)
	except Exception:
		return False


def _play_source_wav_stream(source_path, start_ms, end_ms, generation, on_playback_started=None):
	with wave.open(source_path, "rb") as handle:
		if handle.getcomptype() != "NONE":
			raise RuntimeError("Compressed WAV playback requires fallback rendering.")
		sample_rate = int(handle.getframerate())
		channels = int(handle.getnchannels())
		sample_width = int(handle.getsampwidth())
		if sample_width not in (1, 2) or channels not in (1, 2):
			raise RuntimeError("This WAV format is not supported by the direct playback stream.")
		total_frames = int(handle.getnframes())
		start_frame = max(0, min(total_frames, int(round((float(start_ms) / 1000.0) * sample_rate))))
		end_frame = max(start_frame, min(total_frames, int(round((float(end_ms) / 1000.0) * sample_rate))))
		if end_frame <= start_frame:
			return "completed"
		chunk_frames = max(1, int(sample_rate / 2))
		buffer_chunks = 6
		handle.setpos(start_frame)
		started = False
		while handle.tell() < end_frame:
			for __ in range(buffer_chunks):
				with _preview_lock:
					if generation != _preview_generation:
						return "superseded"
					player = _get_preview_player(
						sample_rate,
						channels=channels,
						bits_per_sample=sample_width * 8,
					)
				if handle.tell() >= end_frame:
					break
				frames_to_read = min(chunk_frames, end_frame - handle.tell())
				audio_bytes = handle.readframes(frames_to_read)
				if not audio_bytes:
					break
				with _preview_lock:
					if generation != _preview_generation:
						return "superseded"
					player.feed(audio_bytes)
				if not started:
					started = True
					if on_playback_started is not None:
						wx.CallAfter(on_playback_started)
			player.idle()
		with _preview_lock:
			if generation != _preview_generation:
				return "superseded"
		return "completed"


def play_audio_source_segment(source_path, start_ms, end_ms, on_complete=None, on_playback_started=None):
	generation = _begin_preview()

	def _finish(status, error_message=None):
		if on_complete is not None:
			wx.CallAfter(on_complete, status, error_message)

	def _worker():
		try:
			if _can_stream_source_wav(source_path):
				status = _play_source_wav_stream(
					source_path,
					start_ms,
					end_ms,
					generation,
					on_playback_started=on_playback_started,
				)
			else:
				payload = render_audio_source_segment(source_path, start_ms, end_ms)
				status = _play_preview_audio(
					payload["audioBytes"],
					payload["sampleRate"],
					generation,
					on_playback_started=on_playback_started,
				)
			_finish(status, None)
		except Exception as error:
			log.error("MaxLogic XTTS v2 source audio playback failed", exc_info=True)
			_finish("error", str(error))

	thread = threading.Thread(
		target=_worker,
		name="MaxLogicXTTSV2SourcePreview",
		daemon=True,
	)
	thread.start()


def extract_sample_to_voice(
	source_path,
	voice_name,
	start_ms,
	end_ms,
	normalize=False,
	trim_silence=False,
	overwrite=False,
):
	voice_id = normalize_voice_id(voice_name)
	temp_dir = tempfile.mkdtemp(dir=get_temp_dir(create=True))
	temp_wav_path = os.path.join(temp_dir, "%s.wav" % voice_id)
	try:
		extraction = _run_audio_tool(
			[
				"extract",
				"--path",
				source_path,
				"--out",
				temp_wav_path,
				"--start-ms",
				str(float(start_ms)),
				"--end-ms",
				str(float(end_ms)),
			]
			+ (["--normalize"] if normalize else [])
			+ (["--trim-silence"] if trim_silence else [])
		)
		records = install_voice_files(
			temp_wav_path,
			source_type="sample-extract",
			overwrite=overwrite,
			install_note="Extracted from a longer source recording",
			extra_metadata={
				"voiceId": voice_id,
				"displayName": voice_name.strip(),
				"extractedFromPath": source_path,
				"selectionStartMs": int(round(float(start_ms))),
				"selectionEndMs": int(round(float(end_ms))),
				"normalizedSample": bool(normalize),
				"trimmedSilence": bool(trim_silence),
			},
		)
	finally:
		if os.path.isfile(temp_wav_path):
			os.remove(temp_wav_path)
		if os.path.isdir(temp_dir):
			try:
				os.rmdir(temp_dir)
			except OSError:
				pass
	refresh_result = refresh_active_synth(
		reason="sample-extract",
		preferred_voice=records[0].voice_id if len(records) == 1 else None,
	)
	_invalidate_preview_helper("sample-extract")
	return {
		"records": records,
		"extraction": extraction,
		"refresh": refresh_result,
	}


def _get_cache_helper_client():
	synth = _get_active_maxlogic_synth()
	helper = getattr(synth, "_engine", None)
	if isinstance(helper, HelperEngineClient):
		return helper, False
	helper = HelperEngineClient(_package_root(), log, helper_mode="cache", skip_prewarm=True)
	return helper, True


def _close_cache_helper_client(helper, should_close):
	if should_close and helper is not None:
		helper.close()


def _build_cache_stats_payload(helper_response=None, error_message=None):
	settings = get_speech_cache_settings()
	if helper_response is None:
		return {
			"dbPath": "",
			"entryCount": 0,
			"sizeBytes": 0,
			"sizeMb": 0.0,
			"lastUsed": None,
			"settings": settings,
			"available": False,
			"error": error_message or "Speech cache support is unavailable.",
			"hotEntryCount": 0,
			"hotSizeBytes": 0,
			"hotSizeMb": 0.0,
			"hotTtlSeconds": 0,
		}
	persistent = helper_response.get("persistent") or {}
	hot = helper_response.get("hot") or {}
	size_bytes = int(persistent.get("sizeBytes") or 0)
	hot_size_bytes = int(hot.get("sizeBytes") or 0)
	available = bool(persistent)
	return {
		"dbPath": persistent.get("dbPath", ""),
		"entryCount": int(persistent.get("entryCount") or 0),
		"sizeBytes": size_bytes,
		"sizeMb": round(size_bytes / float(1024 * 1024), 2),
		"lastUsed": persistent.get("lastUsed"),
		"settings": settings,
		"available": available,
		"error": None if available else _("Persistent speech cache is unavailable."),
		"hotEntryCount": int(hot.get("entryCount") or 0),
		"hotSizeBytes": hot_size_bytes,
		"hotSizeMb": round(hot_size_bytes / float(1024 * 1024), 2),
		"hotTtlSeconds": int(hot.get("ttlSeconds") or 0),
	}


def refresh_active_synth(reason, preferred_voice=None):
	synth = _get_active_maxlogic_synth()
	if synth is None:
		return {
			"refreshed": False,
			"restartRequired": False,
			"runtimeStatus": None,
		}
	if hasattr(synth, "reloadVoiceStore"):
		synth.reloadVoiceStore(reason=reason, preferred_voice=preferred_voice)
		status = synth.getRuntimeStatus() if hasattr(synth, "getRuntimeStatus") else None
		log.info("MaxLogic XTTS v2 service refreshed active synth. reason=%s status=%s", reason, status)
		return {
			"refreshed": True,
			"restartRequired": False,
			"runtimeStatus": status,
		}
	return {
		"refreshed": False,
		"restartRequired": True,
		"runtimeStatus": None,
	}


def install_local_voice(source_path, overwrite=False):
	records = install_voice_files(
		source_path,
		source_type="local-file",
		overwrite=overwrite,
		install_note="Installed from local XTTS audio or conditioning file",
		validate_conditioning=_validate_conditioning_file,
	)
	refresh_result = refresh_active_synth(
		reason="local-install",
		preferred_voice=records[0].voice_id if len(records) == 1 else None,
	)
	_invalidate_preview_helper("local-install")
	return {
		"records": records,
		"refresh": refresh_result,
	}


def remove_local_voice(voice_id):
	removed_paths = remove_user_voice(voice_id)
	refresh_result = refresh_active_synth(reason="local-remove")
	_invalidate_preview_helper("local-remove")
	return {
		"removedPaths": removed_paths,
		"refresh": refresh_result,
	}


def list_catalog_voices(catalog_name="official", force_refresh=False):
	return get_catalog_entries(catalog_name=catalog_name, force_refresh=force_refresh)


def install_catalog_voice(entry, overwrite=False, force_bad_sha=False, refresh=True):
	records = download_catalog_voice(entry, overwrite=overwrite, force_bad_sha=force_bad_sha)
	refresh_result = {
		"refreshed": False,
		"restartRequired": False,
		"runtimeStatus": None,
	}
	if refresh:
		refresh_result = refresh_active_synth(
			reason="catalog-install",
			preferred_voice=records[0].voice_id if len(records) == 1 else None,
		)
		_invalidate_preview_helper("catalog-install")
	return {
		"records": records,
		"refresh": refresh_result,
	}


def search_huggingface_voices(query, limit=20):
	return search_huggingface_entries(query=query, limit=limit)


def install_huggingface_voice(entry, overwrite=False, refresh=True):
	records = download_catalog_voice(entry, overwrite=overwrite, force_bad_sha=False)
	refresh_result = {
		"refreshed": False,
		"restartRequired": False,
		"runtimeStatus": None,
	}
	if refresh:
		refresh_result = refresh_active_synth(
			reason="huggingface-install",
			preferred_voice=records[0].voice_id if len(records) == 1 else None,
		)
		_invalidate_preview_helper("huggingface-install")
	return {
		"records": records,
		"refresh": refresh_result,
	}


def _build_installed_preview_cache_payload(record, language, sample_text):
	return {
		"cacheVersion": _PREVIEW_WAV_CACHE_VERSION,
		"kind": "draft" if record.source == "draft" else "installed",
		"synthesisSettings": dict((record.metadata or {}).get("synthesisSettings") or {}),
		"voiceId": record.voice_id,
		"language": language,
		"text": sample_text,
		"profile": _fingerprint_file(record.profile_path),
		"metadata": _fingerprint_file(record.metadata_path),
		"references": [_fingerprint_file(path) for path in sorted(record.reference_paths or [])],
		"conditioning": _fingerprint_file(record.conditioning_path),
	}


def _build_catalog_preview_cache_payload(entry, language, sample_text):
	return {
		"cacheVersion": _PREVIEW_WAV_CACHE_VERSION,
		"kind": "catalog",
		"catalog": entry.get("catalog", "official"),
		"id": entry.get("id"),
		"language": language,
		"text": sample_text,
		"downloadUrl": entry.get("downloadUrl"),
		"sourceFile": entry.get("sourceFile"),
		"sha256": entry.get("sha256"),
	}


def play_installed_voice_sample(record, on_complete=None, preview_language=None, on_playback_started=None, on_progress=None, sample_text=None):
	generation = _begin_preview()
	start_time = time.perf_counter()

	def progress(phase):
		if on_progress is not None:
			wx.CallAfter(on_progress, phase)

	def started():
		log.info("MaxLogic XTTS v2 preview first audio. voice=%s elapsedMs=%.1f", record.voice_id, (time.perf_counter() - start_time) * 1000)
		if on_playback_started is not None:
			on_playback_started()

	def _finish(status, error_message=None):
		if on_complete is not None:
			wx.CallAfter(on_complete, status, error_message)

	def _worker():
		try:
			language = (preview_language or ((record.metadata or {}).get("language")) or "en")
			text = get_sample_text(language) if sample_text is None else sample_text.strip()
			if not text:
				raise ValueError("Enter sample text to preview the voice.")
			cache_payload = _build_installed_preview_cache_payload(record, language, text)
			cached_preview = _read_preview_wav_cache(cache_payload)
			if generation != _preview_generation:
				_finish("superseded")
				return
			log.info("MaxLogic XTTS v2 preview cache lookup. voice=%s cache=%s elapsedMs=%.1f", record.voice_id, "hit" if cached_preview is not None else "miss", (time.perf_counter() - start_time) * 1000)
			if cached_preview is not None:
				progress("cached")
				audio_bytes = cached_preview["audioBytes"]
				sample_rate = cached_preview["sampleRate"]
			else:
				progress("loading_model")
				helper = _get_preview_helper(skip_prewarm=True)
				progress("generating")
				if record.source == "draft":
					if not record.conditioning_path:
						raise VoiceStoreError("The draft has no saved conditioning.")
					chunks = helper.stream_synthesize_preview_to_int16(
						text, conditioning_path=record.conditioning_path,
						language=language, cache_key=record.voice_id,
						synthesis_settings=(record.metadata or {}).get("synthesisSettings"),
					)
				else:
					if record.voice_id not in set(getattr(helper, "_voices", []) or []):
						helper.reload_voices(preferred_voice=record.voice_id)
					chunks = helper.stream_synthesize_to_int16(text, voice=record.voice_id, language=language)
				status = _play_preview_stream(chunks, helper.sample_rate, generation, cache_payload, started)
				_finish(status)
				return
			status = _play_preview_audio(
				audio_bytes,
				sample_rate,
				generation,
				on_playback_started=started,
			)
			if status == "completed":
				_finish("completed", None)
			else:
				_finish(status, None)
		except Exception as error:
			close_preview_helper()
			log.error(
				"MaxLogic XTTS v2 installed voice preview failed. voice=%s source=%s",
				record.voice_id,
				record.source,
				exc_info=True,
			)
			_finish("error", str(error))

	thread = threading.Thread(
		target=_worker,
		name="MaxLogicXTTSV2InstalledPreview",
		daemon=True,
	)
	thread.start()


def play_catalog_voice_sample(entry, on_complete=None, preview_language=None, on_playback_started=None):
	generation = _begin_preview()
	cache_key = "catalog:%s:%s" % (
		entry.get("catalog", "official"),
		entry.get("id", "unknown"),
	)

	def _finish(status, error_message=None):
		if on_complete is not None:
			wx.CallAfter(on_complete, status, error_message)

	def _worker():
		temp_payload = None
		try:
			if not entry.get("availableOnline", True):
				raise RuntimeError("This profile is not available from the online catalog source.")
			language = preview_language or entry.get("language") or "en"
			sample_text = get_sample_text(language)
			cache_payload = _build_catalog_preview_cache_payload(entry, language, sample_text)
			cached_preview = _read_preview_wav_cache(cache_payload)
			if cached_preview is not None:
				audio_bytes = cached_preview["audioBytes"]
				sample_rate = cached_preview["sampleRate"]
			else:
				temp_payload = download_catalog_voice_to_temp(entry)
				sample_rate = 24000
				try:
					helper = _get_preview_helper(skip_prewarm=True)
				except Exception as helper_error:
					log.warning("MaxLogic XTTS v2 preview helper unavailable, using in-process preview: %s", helper_error)
					from synthDrivers.maxlogic_xtts_v2._engine import XTTSV2Engine

					engine = XTTSV2Engine(_package_root())
					audio = engine.synthesize_preview_to_int16(
						sample_text,
						voice_path=temp_payload["path"],
						language=language,
						cache_key=cache_key,
					)
					engine.close()
					audio_bytes = audio.tobytes()
				else:
					audio_bytes = helper.synthesize_preview_to_int16(
						sample_text,
						voice_path=temp_payload["path"],
						language=language,
						cache_key=cache_key,
					).tobytes()
					sample_rate = helper.sample_rate
				try:
					_write_preview_wav_cache(cache_payload, sample_rate, audio_bytes)
				except Exception:
					log.warning(
						"MaxLogic XTTS v2 preview WAV cache write failed for catalog=%s id=%s",
						entry.get("catalog", "official"),
						entry.get("id"),
						exc_info=True,
					)
			status = _play_preview_audio(
				audio_bytes,
				sample_rate,
				generation,
				on_playback_started=on_playback_started,
			)
			if status == "completed":
				_finish("completed", None)
			else:
				_finish(status, None)
		except Exception as error:
			close_preview_helper()
			log.error(
				"MaxLogic XTTS v2 preview failed. catalog=%s id=%s",
				entry.get("catalog", "official"),
				entry.get("id"),
				exc_info=True,
			)
			_finish("error", str(error))
		finally:
			if temp_payload is not None:
				if os.path.isfile(temp_payload["path"]):
					os.remove(temp_payload["path"])
				cleanup_dir = temp_payload.get("cleanupDir")
				if cleanup_dir and os.path.isdir(cleanup_dir):
					os.rmdir(cleanup_dir)

	thread = threading.Thread(
		target=_worker,
		name="MaxLogicXTTSV2Preview",
		daemon=True,
	)
	thread.start()


def get_runtime_status():
	synth = _get_active_maxlogic_synth()
	if synth is None:
		return {
			"mode": "inactive",
			"providers": [],
			"voiceCount": 0,
			"currentVoice": None,
		}
	if hasattr(synth, "getRuntimeStatus"):
		return synth.getRuntimeStatus()
	return {
		"mode": getattr(synth, "name", "unknown"),
		"providers": [],
		"voiceCount": len(getattr(synth, "availableVoices", {}) or {}),
		"currentVoice": getattr(synth, "voice", None),
	}


def get_speech_cache_settings():
	settings = load_cache_settings()
	return resolve_cache_policy(settings)


def get_speech_cache_stats():
	try:
		helper, should_close = _get_cache_helper_client()
	except Exception as error:
		log.warning("MaxLogic XTTS v2 helper cache stats unavailable: %s", error)
		return _build_cache_stats_payload(error_message=str(error))
	try:
		response = helper.get_cache_stats()
		return _build_cache_stats_payload(response)
	finally:
		_close_cache_helper_client(helper, should_close)


def save_speech_cache_settings(settings):
	normalized = save_cache_settings(settings)
	policy = resolve_cache_policy(normalized)
	log.info("MaxLogic XTTS v2 speech cache settings saved. policy=%s", policy)
	return {
		"settings": policy,
		"stats": get_speech_cache_stats(),
	}


def clear_speech_cache():
	try:
		helper, should_close = _get_cache_helper_client()
	except Exception as error:
		log.warning("MaxLogic XTTS v2 helper cache clear unavailable: %s", error)
		return _build_cache_stats_payload(error_message=str(error))
	try:
		response = helper.clear_cache()
	finally:
		_close_cache_helper_client(helper, should_close)
	return _build_cache_stats_payload(response)


def compact_speech_cache():
	if _get_active_maxlogic_synth() is not None:
		return {
			"compacted": False,
			"restartRequired": True,
			"stats": get_speech_cache_stats(),
		}
	try:
		helper, should_close = _get_cache_helper_client()
	except Exception as error:
		log.warning("MaxLogic XTTS v2 helper cache compact unavailable: %s", error)
		return {
			"compacted": False,
			"restartRequired": False,
			"stats": _build_cache_stats_payload(error_message=str(error)),
		}
	try:
		response = helper.compact_cache()
	finally:
		_close_cache_helper_client(helper, should_close)
	return {
		"compacted": bool(response.get("compacted", True)),
		"restartRequired": False,
		"stats": _build_cache_stats_payload(response),
	}


__all__ = [
	"CACHE_MODE_OPTIONS",
	"DuplicateVoiceError",
	"VoiceStoreError",
	"clear_speech_cache",
	"clone_voice_draft",
	"save_voice_draft",
	"discard_voice_draft",
	"compact_speech_cache",
	"create_audio_working_copy",
	"delete_audio_source_segment",
	"delete_audio_working_copy",
	"export_audio_source_segment",
	"extract_sample_to_voice",
	"get_speech_cache_settings",
	"get_speech_cache_stats",
	"get_runtime_status",
	"get_preview_language_options",
	"get_setup_status",
	"install_catalog_voice",
	"install_huggingface_voice",
	"install_local_voice",
	"list_catalog_voices",
	"list_installed_user_voices",
	"play_installed_voice_sample",
	"play_catalog_voice_sample",
	"play_audio_source_segment",
	"probe_audio_source",
	"prepare_preview_runtime_async",
	"refresh_active_synth",
	"render_audio_source_segment",
	"remove_local_voice",
	"run_runtime_setup",
	"save_audio_working_copy",
	"search_huggingface_voices",
	"close_preview_helper",
	"save_speech_cache_settings",
	"stop_preview",
]
