import hashlib
import os
import shutil
import sys
import tempfile


DEPS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deps")
if os.path.isdir(DEPS_ROOT) and DEPS_ROOT not in sys.path:
	sys.path.insert(0, DEPS_ROOT)

import numpy as np
import soundfile as sf

try:
	from logHandler import log
except ImportError:
	import logging
	log = logging.getLogger(__name__)

def _patch_transformers_compat():
	try:
		import transformers.pytorch_utils as pytorch_utils
	except Exception:
		return
	if hasattr(pytorch_utils, "isin_mps_friendly"):
		return

	def _isin_mps_friendly(elements, test_elements):
		import torch
		return torch.isin(elements, test_elements)

	pytorch_utils.isin_mps_friendly = _isin_mps_friendly


_patch_transformers_compat()


def _patch_torchaudio_load():
	try:
		import torch
		import torchaudio
	except Exception:
		return
	if getattr(torchaudio.load, "_maxlogic_xtts_v2_patched", False):
		return

	def _soundfile_load(
		uri,
		frame_offset=0,
		num_frames=-1,
		normalize=True,
		channels_first=True,
		format=None,
		buffer_size=4096,
		backend=None,
	):
		frames = -1 if num_frames in (None, -1) else int(num_frames)
		audio, sample_rate = sf.read(
			uri,
			start=int(frame_offset or 0),
			frames=frames,
			dtype="float32",
			always_2d=True,
		)
		tensor = torch.from_numpy(audio)
		if channels_first:
			tensor = tensor.transpose(0, 1)
		return tensor, sample_rate

	_soundfile_load._maxlogic_xtts_v2_patched = True
	torchaudio.load = _soundfile_load


_patch_torchaudio_load()

TTS = None
TTS_IMPORT_ERROR = None
try:
	from TTS.api import TTS
except ImportError as error:
	TTS_IMPORT_ERROR = error

try:
	from ._paths import get_cache_dir
	from ._voice_store import VoiceStoreError, discover_voice_records, ensure_user_voice_dir, resolve_preview_reference_paths
except ImportError:
	from _paths import get_cache_dir
	from _voice_store import VoiceStoreError, discover_voice_records, ensure_user_voice_dir, resolve_preview_reference_paths


SUPPORTED_LANGUAGES = {
	"ar",
	"cs",
	"de",
	"en",
	"es",
	"fr",
	"hu",
	"it",
	"ja",
	"ko",
	"nl",
	"pl",
	"pt",
	"ru",
	"tr",
	"zh-cn",
}

LANGUAGE_NORMALIZATION = {
	"ar-sa": "ar",
	"cs-cz": "cs",
	"de-de": "de",
	"en-gb": "en",
	"en-us": "en",
	"es-es": "es",
	"fr-fr": "fr",
	"hu-hu": "hu",
	"it-it": "it",
	"ja-jp": "ja",
	"ko-kr": "ko",
	"nl-nl": "nl",
	"pl-pl": "pl",
	"pt-br": "pt",
	"pt-pt": "pt",
	"ru-ru": "ru",
	"tr-tr": "tr",
	"zh": "zh-cn",
	"zh-cn": "zh-cn",
}


def _resolve_asset_root(package_root):
	override_root = os.environ.get("MAXLOGIC_XTTS_V2_ASSET_ROOT")
	roots = [package_root]
	if override_root:
		roots.append(override_root)
	return roots


class XTTSV2Engine(object):
	sample_rate = 24000

	def __init__(self, package_root):
		if TTS is None:
			detail = (str(TTS_IMPORT_ERROR or "") or "unknown import error").strip()
			raise RuntimeError("Coqui TTS is not available in this Python environment: %s" % detail)
		self.package_root = package_root
		self.user_voice_dir = ensure_user_voice_dir()
		self.model_name = os.environ.get(
			"MAXLOGIC_XTTS_V2_MODEL_NAME",
			"tts_models/multilingual/multi-dataset/xtts_v2",
		).strip() or "tts_models/multilingual/multi-dataset/xtts_v2"
		self.use_gpu = self._should_use_gpu()
		self.tts = TTS(self.model_name, gpu=self.use_gpu)
		self.voice_cache_dir = os.path.join(get_cache_dir(create=True), "voice-conditioning")
		os.makedirs(self.voice_cache_dir, exist_ok=True)
		self._voice_conditioning = {}
		self.voice_records = {}
		self.voice_sources = []
		self.current_voice = None
		self.reload_voices()
		log.info(
			"MaxLogic XTTS v2 initialized. model=%s gpu=%s currentVoice=%s voiceCount=%s",
			self.model_name,
			self.use_gpu,
			self.current_voice,
			len(self.voice_records),
		)

	@classmethod
	def check_runtime_requirements(cls, package_root):
		missing = []
		if TTS is None:
			missing.append("Coqui TTS Python package")
		fallback_roots = [("package", root) for root in _resolve_asset_root(package_root)]
		voice_records, __ = discover_voice_records(package_root, fallback_roots)
		if not voice_records:
			missing.append("voices/*.(wav|mp3|flac|ogg|m4a|aac|pth)")
		return missing

	def _should_use_gpu(self):
		preference = os.environ.get("MAXLOGIC_XTTS_V2_GPU", "auto").strip().lower()
		if preference in ("0", "false", "no", "cpu"):
			return False
		if preference in ("1", "true", "yes", "cuda"):
			return True
		try:
			import torch
			return bool(torch.cuda.is_available())
		except Exception:
			return False

	def _load_voices(self):
		fallback_roots = [("package", root) for root in _resolve_asset_root(self.package_root)]
		voice_records, scanned_roots = discover_voice_records(self.package_root, fallback_roots)
		self.voice_records = voice_records
		self.voice_sources = scanned_roots
		return voice_records

	def reload_voices(self, preferred_voice=None):
		previous_voice = preferred_voice or self.current_voice
		voices = self._load_voices()
		if not voices:
			self.current_voice = None
			raise RuntimeError("No XTTS voice profiles are available from user or packaged sources")
		if previous_voice in voices:
			self.current_voice = previous_voice
		else:
			self.current_voice = sorted(voices.keys())[0]
		log.info(
			"MaxLogic XTTS v2 voice discovery reloaded. currentVoice=%s voiceCount=%s sources=%s",
			self.current_voice,
			len(voices),
			["%s:%s" % (source, root) for source, root in self.voice_sources],
		)
		return self.current_voice

	def list_voices(self):
		return list(self.voice_records.keys())

	def set_voice(self, voice_name):
		if voice_name not in self.voice_records:
			raise KeyError("Unknown voice: %s" % voice_name)
		self.current_voice = voice_name

	def get_status(self):
		return {
			"mode": "in-process",
			"providers": ["cuda"] if self.use_gpu else ["cpu"],
			"voiceCount": len(self.voice_records),
			"currentVoice": self.current_voice,
			"pid": os.getpid(),
			"modelName": self.model_name,
			"voiceSources": ["%s:%s" % (source, root) for source, root in self.voice_sources],
		}

	def close(self):
		self.tts = None

	def validate_conditioning_file(self, conditioning_path):
		if not conditioning_path or not os.path.isfile(conditioning_path):
			raise VoiceStoreError("XTTS conditioning file not found: %s" % conditioning_path)
		temp_dir = tempfile.mkdtemp(prefix="maxlogic-xtts-v2-validate-")
		voice_id = "validation_voice"
		target_path = os.path.join(temp_dir, "%s.pth" % voice_id)
		try:
			shutil.copyfile(conditioning_path, target_path)
			voice = self.tts.synthesizer.tts_model.load_voice_file(voice_id, temp_dir)
			if not isinstance(voice, dict):
				raise RuntimeError("XTTS conditioning file returned an unexpected payload")
			for key in ("gpt_conditioning_latents", "speaker_embedding"):
				if key not in voice or voice[key] is None:
					raise RuntimeError("XTTS conditioning file is missing required field '%s'" % key)
			return {
				"voiceId": voice_id,
				"keys": sorted(voice.keys()),
			}
		except Exception as error:
			log.warning("XTTS conditioning validation failed for %s: %s", conditioning_path, error)
			raise VoiceStoreError("Unsupported or corrupted XTTS conditioning file")
		finally:
			shutil.rmtree(temp_dir, ignore_errors=True)

	def synthesize_to_int16(self, text, speed=1.0, voice=None, volume=1.0, language="en-us", generation=None):
		voice_name = voice or self.current_voice
		if voice_name is None:
			raise RuntimeError("No XTTS voice profile is selected")
		record = self.voice_records.get(voice_name)
		if record is None:
			raise KeyError("Unknown voice: %s" % voice_name)
		return self._synthesize_from_references(
			text,
			record.reference_paths,
			conditioning_path=record.conditioning_path,
			cache_key=voice_name,
			speed=speed,
			volume=volume,
			language=language,
		)

	def synthesize_preview_to_int16(self, text, voice_path, speed=1.0, volume=1.0, language="en-us", cache_key=None):
		reference_paths, cleanup_root = resolve_preview_reference_paths(voice_path)
		try:
			return self._synthesize_from_references(
				text,
				reference_paths,
				cache_key=(cache_key or self._build_voice_cache_key(reference_paths)),
				speed=speed,
				volume=volume,
				language=language,
			)
		finally:
			if cleanup_root:
				import shutil
				shutil.rmtree(cleanup_root, ignore_errors=True)

	def _normalize_language(self, language):
		key = (language or "en").strip().lower()
		normalized = LANGUAGE_NORMALIZATION.get(key, key.split("-", 1)[0])
		if normalized == "zh":
			normalized = "zh-cn"
		if normalized not in SUPPORTED_LANGUAGES:
			normalized = "en"
		return normalized

	def _synthesize_from_references(self, text, reference_paths, conditioning_path=None, cache_key=None, speed=1.0, volume=1.0, language="en-us"):
		normalized_language = self._normalize_language(language)
		speed = max(0.6, min(1.8, float(speed)))
		voice = self._get_or_create_voice_conditioning(
			reference_paths,
			cache_key=cache_key,
			conditioning_path=conditioning_path,
		)
		waveform = self._tts_with_cached_voice(
			text=text,
			voice=voice,
			language=normalized_language,
			speed=speed,
		)
		waveform = np.asarray(waveform, dtype=np.float32).reshape(-1)
		if speed != 1.0:
			waveform = self._time_scale(waveform, speed)
		waveform = np.clip(waveform * max(0.0, min(1.0, float(volume))), -1.0, 1.0)
		return (waveform * 32767.0).astype(np.int16, copy=False)

	def _build_voice_cache_key(self, reference_paths, conditioning_path=None):
		digest = hashlib.sha256()
		digest.update(self.model_name.encode("utf-8"))
		paths = [os.path.abspath(path) for path in (reference_paths or []) if path]
		if conditioning_path:
			paths.append(os.path.abspath(conditioning_path))
		for path in sorted(paths):
			digest.update(path.encode("utf-8", errors="replace"))
			try:
				stats = os.stat(path)
			except OSError:
				continue
			digest.update(str(int(stats.st_mtime_ns)).encode("ascii"))
			digest.update(str(int(stats.st_size)).encode("ascii"))
		return "ref_" + digest.hexdigest()[:24]

	def _prepare_reference_paths(self, reference_paths):
		paths = [os.path.abspath(path) for path in (reference_paths or []) if path]
		if not paths:
			raise VoiceStoreError("No XTTS reference audio files were provided")
		if all(os.path.splitext(path)[1].lower() == ".wav" for path in paths):
			return paths, None
		cleanup_root = tempfile.mkdtemp(prefix="maxlogic-xtts-v2-preview-")
		prepared_paths = []
		for index, path in enumerate(paths):
			extension = os.path.splitext(path)[1].lower()
			if extension == ".wav":
				prepared_paths.append(path)
				continue
			try:
				audio, sample_rate = sf.read(path)
			except Exception as error:
				raise RuntimeError("Unable to decode reference audio '%s': %s" % (path, error))
			target_path = os.path.join(cleanup_root, "reference_%02d.wav" % index)
			sf.write(target_path, audio, sample_rate, subtype="PCM_16")
			prepared_paths.append(target_path)
		return prepared_paths, cleanup_root

	def _get_or_create_voice_conditioning(self, reference_paths, cache_key=None, conditioning_path=None):
		cache_key = cache_key or self._build_voice_cache_key(reference_paths, conditioning_path=conditioning_path)
		voice = self._voice_conditioning.get(cache_key)
		if voice is not None:
			return voice
		model = self.tts.synthesizer.tts_model
		voice_file_path = os.path.join(self.voice_cache_dir, "%s.pth" % cache_key)
		if conditioning_path:
			shutil.copyfile(conditioning_path, voice_file_path)
			voice = model.load_voice_file(cache_key, self.voice_cache_dir)
		elif os.path.isfile(voice_file_path):
			voice = model.load_voice_file(cache_key, self.voice_cache_dir)
		else:
			prepared_reference_paths, cleanup_root = self._prepare_reference_paths(reference_paths)
			try:
				voice = model.clone_voice(
					prepared_reference_paths,
					speaker_id=cache_key,
					voice_dir=self.voice_cache_dir,
				)
			finally:
				if cleanup_root:
					shutil.rmtree(cleanup_root, ignore_errors=True)
		self._voice_conditioning[cache_key] = {
			"gpt_conditioning_latents": voice["gpt_conditioning_latents"].detach().cpu(),
			"speaker_embedding": voice["speaker_embedding"].detach().cpu(),
		}
		return self._voice_conditioning[cache_key]

	def _tts_with_cached_voice(self, text, voice, language, speed):
		model = self.tts.synthesizer.tts_model
		return model.inference(
			text,
			language,
			voice["gpt_conditioning_latents"],
			voice["speaker_embedding"],
			speed=speed,
			enable_text_splitting=False,
		)["wav"]

	def _time_scale(self, waveform, speed):
		if waveform.size < 2 or abs(speed - 1.0) < 0.01:
			return waveform
		target_size = max(1, int(round(float(waveform.size) / speed)))
		source_positions = np.arange(waveform.size, dtype=np.float32)
		target_positions = np.linspace(0, waveform.size - 1, target_size, dtype=np.float32)
		return np.interp(target_positions, source_positions, waveform).astype(np.float32, copy=False)
