# Shared with kokoro-tts-nvda. Source of truth: xtts-v2-nvda/addon/synthDrivers/maxlogic_xtts_v2/_loading_sounds.py.
# Change it there first, then copy it to kokoro. Only product names may differ.
"""Sounds that announce when the speech engine starts loading and when it is ready."""
import json
import os
import wave

try:
	from ._paths import get_user_data_dir
except ImportError:
	from _paths import get_user_data_dir


LOADING = "loading"
READY = "ready"
SOUND_KINDS = (LOADING, READY)
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
# An empty path means the sound that comes with the add-on.
DEFAULT_SOUND_SETTINGS = {kind: {"enabled": True, "path": ""} for kind in SOUND_KINDS}


def get_sound_settings_path():
	return os.path.join(get_user_data_dir(create=True), "loading-sounds.json")


def default_sound_path(kind):
	return os.path.join(SOUNDS_DIR, "%s.wav" % kind)


def normalize_sound_settings(settings):
	normalized = {}
	for kind in SOUND_KINDS:
		entry = settings.get(kind) if isinstance(settings, dict) else None
		if not isinstance(entry, dict):
			entry = {}
		enabled = entry.get("enabled", True)
		path = entry.get("path", "")
		normalized[kind] = {
			"enabled": enabled if isinstance(enabled, bool) else True,
			"path": path if isinstance(path, str) else "",
		}
	return normalized


def load_sound_settings():
	try:
		with open(get_sound_settings_path(), "r", encoding="utf-8") as handle:
			payload = json.load(handle)
	except Exception:
		payload = {}
	return normalize_sound_settings(payload)


def save_sound_settings(settings):
	normalized = normalize_sound_settings(settings)
	with open(get_sound_settings_path(), "w", encoding="utf-8") as handle:
		json.dump(normalized, handle, indent=2, sort_keys=True)
	return normalized


def is_playable_wave(path):
	"""NVDA plays only uncompressed WAV files."""
	try:
		with wave.open(path, "rb") as handle:
			return handle.getnframes() > 0
	except Exception:
		return False


def resolve_sound_path(settings, kind):
	"""The file to play for kind, or None when that sound is turned off."""
	entry = normalize_sound_settings(settings)[kind]
	if not entry["enabled"]:
		return None
	if entry["path"] and os.path.isfile(entry["path"]):
		return entry["path"]
	# A chosen file that was moved or deleted falls back to the add-on's own sound.
	return default_sound_path(kind)


def play_loading_sound(kind, wait=False, logger=None):
	"""Play the sound for kind. With wait, return when it has finished. Never raises."""
	try:
		path = resolve_sound_path(load_sound_settings(), kind)
		if path is None:
			return False
		import nvwave
		nvwave.playWaveFile(path, asynchronous=not wait)
		return True
	except Exception as error:
		if logger is not None:
			logger.warning("MaxLogic XTTS v2 could not play the %s sound: %s", kind, error)
		return False
