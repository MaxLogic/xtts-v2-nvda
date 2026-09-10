"""Voice cloning orchestration; safe to import in NVDA without torch."""
import json
import os
import shutil
import tempfile

try:
	from ._paths import get_temp_dir
	from ._voice_store import (
		AUDIO_EXTENSIONS, DuplicateVoiceError, VoiceStoreError, get_user_voice_profile_dir,
		install_voice_files, normalize_voice_id,
	)
except ImportError:
	from _paths import get_temp_dir
	from _voice_store import (
		AUDIO_EXTENSIONS, DuplicateVoiceError, VoiceStoreError, get_user_voice_profile_dir,
		install_voice_files, normalize_voice_id,
	)


def validate_options(options):
	result = dict(max_ref_length=30, gpt_cond_len=6, gpt_cond_chunk_len=6, sound_norm_refs=False)
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
	return result


def create_voice(reference_paths, name, language, options, clone):
	"""Publish only after the external helper has successfully saved conditioning."""
	options = validate_options(options)
	voice_id = normalize_voice_id(name)
	if os.path.exists(get_user_voice_profile_dir(voice_id)):
		raise DuplicateVoiceError("A voice with this name already exists. Choose a different name.")
	paths = list(dict.fromkeys(os.path.abspath(path) for path in reference_paths))
	if not paths:
		raise VoiceStoreError("Add at least one reference recording.")
	for path in paths:
		if not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in AUDIO_EXTENSIONS:
			raise VoiceStoreError("Reference recording is missing or unsupported: %s" % path)
	with tempfile.TemporaryDirectory(prefix="clone-", dir=get_temp_dir(create=True)) as staging:
		profile = os.path.join(staging, voice_id)
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
		metadata = dict(voiceId=voice_id, displayName=name.strip(), language=language,
			conditioningFile="conditioning.pth", cloneSettings=options,
			referenceFiles=[os.path.basename(path) for path in copies])
		with open(os.path.join(profile, "profile.json"), "w", encoding="utf-8") as handle:
			json.dump(metadata, handle, indent=2)
		archive = shutil.make_archive(os.path.join(staging, "voice"), "zip", profile)
		return install_voice_files(archive, source_type="voice-clone", extra_metadata=metadata)[0]
