"""Voice cloning orchestration; safe to import in NVDA without torch."""
import json
import os
import shutil
import tempfile
from dataclasses import dataclass

try:
	from ._paths import get_temp_dir
	from ._voice_store import (
		AUDIO_EXTENSIONS, DuplicateVoiceError, VoiceRecord, VoiceStoreError, get_user_voice_profile_dir,
		install_voice_files, normalize_voice_id,
	)
except ImportError:
	from _paths import get_temp_dir
	from _voice_store import (
		AUDIO_EXTENSIONS, DuplicateVoiceError, VoiceRecord, VoiceStoreError, get_user_voice_profile_dir,
		install_voice_files, normalize_voice_id,
	)


def validate_options(options):
	result = dict(max_ref_length=30, gpt_cond_len=6, gpt_cond_chunk_len=6, sound_norm_refs=False)
	# Keep older profiles/settings valid; trimming is an opt-in preprocessing step.
	if "trim_silence" in options:
		result["trim_silence"] = False
	if set(options) - set(result):
		raise VoiceStoreError("Unknown voice cloning setting")
	result.update(options)
	for key in ("max_ref_length", "gpt_cond_len", "gpt_cond_chunk_len"):
		if type(result[key]) is not int or not 1 <= result[key] <= 120:
			raise VoiceStoreError("Reference and conditioning lengths must be between 1 and 120 seconds.")
	if result["gpt_cond_chunk_len"] > result["gpt_cond_len"]:
		raise VoiceStoreError("Conditioning chunk length cannot exceed total conditioning length.")
	if type(result["sound_norm_refs"]) is not bool:
		raise VoiceStoreError("Normalization must be enabled or disabled.")
	if "trim_silence" in result and type(result["trim_silence"]) is not bool:
		raise VoiceStoreError("Silence trimming must be enabled or disabled.")
	if "balance_style" in result and type(result["balance_style"]) is not bool:
		raise VoiceStoreError("Style balancing must be enabled or disabled.")
	return result


@dataclass
class VoiceDraft(VoiceRecord):
	"""Previewable profile whose files belong to a temporary directory."""
	_temporary_directory: object = None


def discard_draft(record):
	"""Release draft files; repeated calls are harmless and installed voices are rejected."""
	if not isinstance(record, VoiceDraft):
		raise VoiceStoreError("Only an unsaved voice draft can be discarded.")
	if record._temporary_directory is not None:
		record._temporary_directory.cleanup()
		record._temporary_directory = None


def create_draft(reference_paths, language, options, clone, synthesis_settings=None):
	"""Compute conditioning once in temporary storage, without installing a voice."""
	options = validate_options(options)
	try:
		from ._voice_presets import generation_settings
	except ImportError:
		from _voice_presets import generation_settings
	synthesis_settings = generation_settings(synthesis_settings)
	paths = list(dict.fromkeys(os.path.abspath(path) for path in reference_paths))
	if not paths:
		raise VoiceStoreError("Add at least one reference recording.")
	for path in paths:
		if not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in AUDIO_EXTENSIONS:
			raise VoiceStoreError("Reference recording is missing or unsupported: %s" % path)
	owner = tempfile.TemporaryDirectory(prefix="clone-", dir=get_temp_dir(create=True))
	try:
		voice_id = os.path.basename(owner.name)
		profile = os.path.join(owner.name, "profile")
		os.mkdir(profile)
		copies = []
		for index, path in enumerate(paths):
			target = os.path.join(profile, "reference_%02d%s" % (index + 1, os.path.splitext(path)[1].lower()))
			shutil.copyfile(path, target)
			copies.append(target)
		conditioning = os.path.join(profile, "conditioning.pth")
		clone(copies, conditioning, options)
		if not os.path.isfile(conditioning) or not os.path.getsize(conditioning):
			raise VoiceStoreError("Cloning did not produce voice conditioning data.")
		metadata = dict(voiceId=voice_id, displayName="Unsaved voice", language=language,
			conditioningFile="conditioning.pth", cloneSettings=options,
			synthesisSettings=synthesis_settings,
			referenceFiles=[os.path.basename(path) for path in copies])
		metadata_path = os.path.join(profile, "profile.json")
		with open(metadata_path, "w", encoding="utf-8") as handle:
			json.dump(metadata, handle, indent=2)
		return VoiceDraft(voice_id=voice_id, profile_path=metadata_path,
			reference_paths=copies, conditioning_path=conditioning, source="draft",
			source_root=profile, metadata_path=metadata_path, metadata=metadata,
			_temporary_directory=owner)
	except BaseException:
		owner.cleanup()
		raise


def save_draft(record, name, overwrite=False):
	"""Publish existing conditioning; retain the draft for preview/retry until discarded."""
	if not isinstance(record, VoiceDraft) or record._temporary_directory is None:
		raise VoiceStoreError("The voice draft is no longer available. Create it again.")
	voice_id = normalize_voice_id(name)
	target = get_user_voice_profile_dir(voice_id)
	if os.path.exists(target) and (not overwrite or not os.path.isdir(target)):
		raise DuplicateVoiceError("A voice with this name already exists. Choose a different name or confirm replacement.")
	if not os.path.isfile(record.conditioning_path) or not os.path.getsize(record.conditioning_path):
		raise VoiceStoreError("The voice draft has no conditioning data. Create it again.")
	metadata = dict(record.metadata, voiceId=voice_id, displayName=name.strip())
	with tempfile.TemporaryDirectory(prefix="save-clone-", dir=get_temp_dir(create=True)) as staging:
		profile = os.path.join(staging, "profile")
		shutil.copytree(record.source_root, profile)
		with open(os.path.join(profile, "profile.json"), "w", encoding="utf-8") as handle:
			json.dump(metadata, handle, indent=2)
		archive = shutil.make_archive(os.path.join(staging, "voice"), "zip", profile)
		return install_voice_files(archive, source_type="voice-clone",
			overwrite=overwrite, extra_metadata=metadata)[0]


def create_voice(reference_paths, name, language, options, clone, synthesis_settings=None):
	"""Compatibility entry point for callers that explicitly request immediate publication."""
	voice_id = normalize_voice_id(name)
	if os.path.exists(get_user_voice_profile_dir(voice_id)):
		raise DuplicateVoiceError("A voice with this name already exists. Choose a different name.")
	draft = create_draft(reference_paths, language, options, clone, synthesis_settings)
	try:
		return save_draft(draft, name)
	finally:
		discard_draft(draft)
