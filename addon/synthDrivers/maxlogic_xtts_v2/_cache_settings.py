import json
import os

try:
	from ._paths import get_user_data_dir
except ImportError:
	from _paths import get_user_data_dir


CACHE_MODE_SHORT_UI = "short_ui"
CACHE_MODE_SHORT_MEDIUM = "short_medium"
CACHE_MODE_CUSTOM = "custom"

DEFAULT_CACHE_SETTINGS = {
	"enabled": True,
	"maxSizeMb": 256,
	"mode": CACHE_MODE_SHORT_UI,
	"customMinChars": 2,
	"customMaxChars": 80,
}

_MODE_LIMITS = {
	CACHE_MODE_SHORT_UI: (2, 80),
	CACHE_MODE_SHORT_MEDIUM: (2, 180),
}


def get_cache_settings_path():
	return os.path.join(get_user_data_dir(create=True), "speech-cache-settings.json")


def load_cache_settings():
	settings = dict(DEFAULT_CACHE_SETTINGS)
	path = get_cache_settings_path()
	if os.path.isfile(path):
		try:
			with open(path, "r", encoding="utf-8") as handle:
				payload = json.load(handle)
		except Exception:
			payload = {}
		if isinstance(payload, dict):
			settings.update(payload)
	return normalize_cache_settings(settings)


def save_cache_settings(settings):
	normalized = normalize_cache_settings(settings)
	path = get_cache_settings_path()
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(normalized, handle, indent=2, sort_keys=True)
	return normalized


def normalize_cache_settings(settings):
	normalized = dict(DEFAULT_CACHE_SETTINGS)
	if isinstance(settings, dict):
		normalized.update(settings)
	normalized["enabled"] = bool(normalized.get("enabled", True))
	max_size = normalized.get("maxSizeMb", DEFAULT_CACHE_SETTINGS["maxSizeMb"])
	try:
		max_size = int(max_size)
	except Exception:
		max_size = DEFAULT_CACHE_SETTINGS["maxSizeMb"]
	normalized["maxSizeMb"] = max(16, min(4096, max_size))
	mode = str(normalized.get("mode", CACHE_MODE_SHORT_UI) or CACHE_MODE_SHORT_UI).strip().lower()
	if mode not in (CACHE_MODE_SHORT_UI, CACHE_MODE_SHORT_MEDIUM, CACHE_MODE_CUSTOM):
		mode = CACHE_MODE_SHORT_UI
	normalized["mode"] = mode
	for key, default_value in (("customMinChars", 2), ("customMaxChars", 80)):
		try:
			normalized[key] = int(normalized.get(key, default_value))
		except Exception:
			normalized[key] = default_value
	normalized["customMinChars"] = max(1, min(256, normalized["customMinChars"]))
	normalized["customMaxChars"] = max(normalized["customMinChars"], min(512, normalized["customMaxChars"]))
	return normalized


def resolve_cache_policy(settings):
	normalized = normalize_cache_settings(settings)
	mode = normalized["mode"]
	if mode == CACHE_MODE_CUSTOM:
		min_chars = normalized["customMinChars"]
		max_chars = normalized["customMaxChars"]
	else:
		min_chars, max_chars = _MODE_LIMITS[mode]
	return {
		"enabled": normalized["enabled"],
		"maxSizeMb": normalized["maxSizeMb"],
		"maxBytes": normalized["maxSizeMb"] * 1024 * 1024,
		"mode": mode,
		"minChars": min_chars,
		"maxChars": max_chars,
		"customMinChars": normalized["customMinChars"],
		"customMaxChars": normalized["customMaxChars"],
	}
