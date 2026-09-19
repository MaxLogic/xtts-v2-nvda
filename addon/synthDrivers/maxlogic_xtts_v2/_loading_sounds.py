# Shared with kokoro-tts-nvda. Source of truth: xtts-v2-nvda/addon/synthDrivers/maxlogic_xtts_v2/_loading_sounds.py.
# Change it there first, then copy it to kokoro. Only product names may differ.
"""Sounds that announce when the speech engine starts loading, that it is still loading, and when it is ready."""
import json
import os
import threading
import wave

try:
	from ._paths import get_user_data_dir
except ImportError:
	from _paths import get_user_data_dir


LOADING = "loading"
# Repeats while the model is still loading.
WAITING = "waiting"
READY = "ready"
SOUND_KINDS = (LOADING, WAITING, READY)
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
DEFAULT_WAITING_INTERVAL = 10
WAITING_INTERVAL_RANGE = (3, 120)
# An empty path means the sound that comes with the add-on.
DEFAULT_SOUND_SETTINGS = {kind: {"enabled": True, "path": ""} for kind in SOUND_KINDS}
DEFAULT_SOUND_SETTINGS[WAITING]["intervalSeconds"] = DEFAULT_WAITING_INTERVAL


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
	interval = (settings.get(WAITING) or {}).get("intervalSeconds") if isinstance(settings, dict) else None
	try:
		interval = int(interval)
	except (TypeError, ValueError):
		interval = DEFAULT_WAITING_INTERVAL
	low, high = WAITING_INTERVAL_RANGE
	normalized[WAITING]["intervalSeconds"] = max(low, min(high, interval))
	return normalized


def waiting_interval(settings):
	"""Seconds between waiting sounds, or None when that sound is turned off."""
	entry = normalize_sound_settings(settings)[WAITING]
	return entry["intervalSeconds"] if entry["enabled"] else None


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


class LoadingAnnouncer(object):
	"""Plays the loading sound, repeats the waiting sound until finish() or stop(), then the ready sound."""

	def __init__(self, play=None, logger=None, interval=None):
		self._play = play or play_loading_sound
		self._logger = logger
		self._interval = interval
		self._stopped = threading.Event()
		self._thread = None

	def start(self):
		self._play(LOADING, logger=self._logger)
		interval = self._interval
		if interval is None:
			interval = waiting_interval(load_sound_settings())
		if interval:
			self._thread = threading.Thread(target=self._repeat, args=(interval,), name="LoadingAnnouncer", daemon=True)
			self._thread.start()

	def _repeat(self, interval):
		while not self._stopped.wait(interval):
			# Waiting keeps this thread busy while the sound plays, so finish() can wait for its end.
			self._play(WAITING, wait=True, logger=self._logger)

	def stop(self, wait=True):
		"""End the waiting sounds. With wait, also wait for one that is playing to end."""
		self._stopped.set()
		thread = self._thread
		if wait and thread is not None and thread is not threading.current_thread():
			thread.join(timeout=10)

	def finish(self, wait=True):
		"""End the waiting sounds, then play the ready sound. With wait, return when it has ended."""
		self.stop()
		self._play(READY, wait=wait, logger=self._logger)
