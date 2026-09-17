"""Explicit starting points, not measured voice-quality rankings.

The first entry of each tuple is the default. "model_config" mirrors the
released XTTS v2 config.json, which Coqui's high-level synthesize() uses;
"function_defaults" mirrors the bare get_conditioning_latents()/inference()
keyword defaults.
"""
import math

# (maximum seconds per recording, total conditioning seconds, chunk seconds)
CONDITIONING_PRESETS = (
	("model_config", (30, 30, 4)),
	("function_defaults", (30, 6, 6)),
	("extended", (30, 12, 6)),
	("long", (30, 30, 6)),
)

GENERATION_PRESETS = (
	("model_config", dict(temperature=0.75, top_p=0.85, top_k=50, repetition_penalty=5.0, speed=1.0)),
	("function_defaults", dict(temperature=0.75, top_p=0.85, top_k=50, repetition_penalty=10.0, speed=1.0)),
	("suggested_mid", dict(temperature=0.75, top_p=0.85, top_k=50, repetition_penalty=2.0, speed=1.0)),
	("suggested_low", dict(temperature=0.65, top_p=0.80, top_k=50, repetition_penalty=2.0, speed=1.0)),
	("suggested_high", dict(temperature=0.85, top_p=0.90, top_k=50, repetition_penalty=2.0, speed=1.0)),
)


def generation_settings(settings):
	if not settings:
		return {}
	limits = dict(temperature=(0.05, 2), top_p=(0.01, 1), top_k=(1, 1000),
		repetition_penalty=(1, 20), speed=(0.6, 1.8))
	if set(settings) - set(limits):
		raise ValueError("Unknown speech generation setting")
	for key, value in settings.items():
		low, high = limits[key]
		if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
			raise ValueError("Invalid speech generation setting: %s" % key)
		if key == "top_k" and type(value) is not int:
			raise ValueError("Top k must be a whole number")
	return dict(settings)
