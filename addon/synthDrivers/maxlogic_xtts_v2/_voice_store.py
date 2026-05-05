import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

try:
	from ._paths import get_temp_dir, get_user_voice_dir
except ImportError:
	from _paths import get_temp_dir, get_user_voice_dir


VOICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
CONDITIONING_EXTENSIONS = (".pth",)
PROFILE_FILE_NAME = "profile.json"


class VoiceStoreError(RuntimeError):
	pass


class DuplicateVoiceError(VoiceStoreError):
	pass


@dataclass
class VoiceRecord(object):
	voice_id: str
	profile_path: str
	reference_paths: list
	conditioning_path: str
	source: str
	source_root: str
	metadata_path: str = None
	metadata: dict = None

	@property
	def display_name(self):
		if self.metadata and self.metadata.get("displayName"):
			return self.metadata["displayName"]
		return self.voice_id.replace("_", " ").title()

	@property
	def file_path(self):
		return self.profile_path


def ensure_user_voice_dir():
	return get_user_voice_dir(create=True)


def get_user_voice_profile_dir(voice_id):
	return os.path.join(get_user_voice_dir(create=True), voice_id)


def get_user_metadata_path(voice_id):
	return os.path.join(get_user_voice_profile_dir(voice_id), PROFILE_FILE_NAME)


def _normalize_voice_id(name):
	voice_id = os.path.splitext(os.path.basename(name))[0].strip()
	voice_id = re.sub(r"[^A-Za-z0-9_-]+", "_", voice_id).strip("_")
	if not voice_id or not VOICE_NAME_RE.match(voice_id):
		raise VoiceStoreError("Unsupported voice profile name: %s" % name)
	return voice_id


def normalize_voice_id(name):
	return _normalize_voice_id(name)


def _load_json(path):
	with open(path, "r", encoding="utf-8-sig") as handle:
		return json.load(handle)


def _write_json(path, payload):
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2, sort_keys=True)


def _is_audio_file(path):
	return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


def _is_conditioning_file(path):
	return os.path.splitext(path)[1].lower() in CONDITIONING_EXTENSIONS


def _list_audio_files(root):
	audio_paths = []
	for entry in sorted(os.listdir(root)):
		path = os.path.join(root, entry)
		if os.path.isfile(path) and _is_audio_file(path):
			audio_paths.append(path)
	return audio_paths


def _resolve_conditioning_file(root, metadata):
	configured_name = (metadata or {}).get("conditioningFile")
	if configured_name:
		configured_path = os.path.join(root, configured_name)
		if os.path.isfile(configured_path) and _is_conditioning_file(configured_path):
			return configured_path
		raise VoiceStoreError("Voice profile conditioning file is missing: %s" % configured_name)
	for entry in sorted(os.listdir(root)):
		path = os.path.join(root, entry)
		if os.path.isfile(path) and _is_conditioning_file(path):
			return path
	return None


def _profile_payload_from_dir(profile_dir):
	metadata_path = os.path.join(profile_dir, PROFILE_FILE_NAME)
	metadata = {}
	if os.path.isfile(metadata_path):
		metadata = _load_json(metadata_path)
	voice_id = _normalize_voice_id(metadata.get("voiceId") or os.path.basename(profile_dir))
	reference_paths = _list_audio_files(profile_dir)
	conditioning_path = _resolve_conditioning_file(profile_dir, metadata)
	if not reference_paths and not conditioning_path:
		raise VoiceStoreError("Voice profile has neither reference audio nor XTTS conditioning data: %s" % profile_dir)
	metadata.setdefault("voiceId", voice_id)
	metadata.setdefault("displayName", voice_id.replace("_", " ").title())
	if conditioning_path:
		metadata.setdefault("conditioningFile", os.path.basename(conditioning_path))
	return VoiceRecord(
		voice_id=voice_id,
		profile_path=metadata_path,
		reference_paths=reference_paths,
		conditioning_path=conditioning_path,
		source="unknown",
		source_root=profile_dir,
		metadata_path=metadata_path if os.path.isfile(metadata_path) else None,
		metadata=metadata,
	)


def _build_voice_record(profile_dir, source, source_root):
	record = _profile_payload_from_dir(profile_dir)
	record.source = source
	record.source_root = source_root
	return record


def discover_voice_records(package_root, fallback_roots):
	records = OrderedDict()
	scanned_roots = []
	root_entries = [("user", get_user_voice_dir(create=True))]
	for source_name, root in fallback_roots:
		root_entries.append((source_name, os.path.join(root, "voices")))
	for source_name, root in root_entries:
		if not root or not os.path.isdir(root):
			continue
		scanned_roots.append((source_name, root))
		for entry in sorted(os.listdir(root)):
			profile_dir = os.path.join(root, entry)
			if not os.path.isdir(profile_dir):
				continue
			try:
				record = _build_voice_record(profile_dir, source_name, root)
			except VoiceStoreError:
				continue
			if record.voice_id not in records:
				records[record.voice_id] = record
	return records, scanned_roots


def list_user_voice_records():
	records, __ = discover_voice_records(None, [])
	return [record for record in records.values() if record.source == "user"]


def create_voice_metadata(voice_id, source_type, source_path, reference_files, install_note=None, extra=None):
	metadata = {
		"voiceId": voice_id,
		"displayName": voice_id.replace("_", " ").title(),
		"sourceType": source_type,
		"sourcePath": source_path,
		"installedAt": datetime.now(timezone.utc).isoformat(),
		"referenceFiles": list(reference_files),
	}
	if install_note:
		metadata["installNote"] = install_note
	if extra:
		metadata.update(extra)
	return metadata


def _make_temp_path(suffix):
	fd, path = tempfile.mkstemp(dir=get_temp_dir(create=True), suffix=suffix)
	os.close(fd)
	return path


def _copy_tree_atomic(source_dir, target_dir):
	temp_parent = get_temp_dir(create=True)
	staging_dir = tempfile.mkdtemp(dir=temp_parent)
	try:
		payload_root = os.path.join(staging_dir, "payload")
		shutil.copytree(source_dir, payload_root)
		if os.path.isdir(target_dir):
			shutil.rmtree(target_dir)
		os.replace(payload_root, target_dir)
	finally:
		shutil.rmtree(staging_dir, ignore_errors=True)


def _is_safe_archive_member(base_dir, member_name):
	if not member_name:
		return False
	target_path = os.path.abspath(os.path.join(base_dir, member_name))
	base_dir = os.path.abspath(base_dir)
	return os.path.commonpath([base_dir, target_path]) == base_dir


def _extract_archive(archive_path):
	temp_dir = tempfile.mkdtemp(dir=get_temp_dir(create=True))
	with zipfile.ZipFile(archive_path, "r") as archive:
		for member in archive.infolist():
			if not _is_safe_archive_member(temp_dir, member.filename):
				shutil.rmtree(temp_dir, ignore_errors=True)
				raise VoiceStoreError("Archive contains an unsafe path: %s" % member.filename)
			if member.is_dir():
				continue
			archive.extract(member, temp_dir)
	return temp_dir


def _find_profile_root(extracted_root, source_path):
	profile_json = os.path.join(extracted_root, PROFILE_FILE_NAME)
	if os.path.isfile(profile_json):
		return extracted_root
	audio_files = _list_audio_files(extracted_root)
	if audio_files:
		return extracted_root
	directories = []
	for entry in sorted(os.listdir(extracted_root)):
		path = os.path.join(extracted_root, entry)
		if os.path.isdir(path):
			directories.append(path)
	for directory in directories:
		if os.path.isfile(os.path.join(directory, PROFILE_FILE_NAME)) or _list_audio_files(directory):
			return directory
	raise VoiceStoreError("Archive contains no usable XTTS profile: %s" % source_path)


def _stage_profile_from_audio(source_path):
	voice_id = _normalize_voice_id(source_path)
	staging_root = tempfile.mkdtemp(dir=get_temp_dir(create=True))
	target_dir = os.path.join(staging_root, voice_id)
	os.makedirs(target_dir, exist_ok=True)
	filename = os.path.basename(source_path)
	shutil.copyfile(source_path, os.path.join(target_dir, filename))
	metadata = create_voice_metadata(
		voice_id,
		source_type="local-file",
		source_path=source_path,
		reference_files=[filename],
		install_note="Installed from local audio reference",
	)
	_write_json(os.path.join(target_dir, PROFILE_FILE_NAME), metadata)
	return target_dir, staging_root


def _stage_profile_from_conditioning(source_path):
	voice_id = _normalize_voice_id(source_path)
	staging_root = tempfile.mkdtemp(dir=get_temp_dir(create=True))
	target_dir = os.path.join(staging_root, voice_id)
	os.makedirs(target_dir, exist_ok=True)
	filename = "%s.pth" % voice_id
	shutil.copyfile(source_path, os.path.join(target_dir, filename))
	metadata = create_voice_metadata(
		voice_id,
		source_type="local-file",
		source_path=source_path,
		reference_files=[],
		install_note="Installed from local XTTS conditioning file",
		extra={"conditioningFile": filename},
	)
	_write_json(os.path.join(target_dir, PROFILE_FILE_NAME), metadata)
	return target_dir, staging_root


def _prepare_staged_profile(source_path, source_type, install_note=None, extra_metadata=None, validate_conditioning=None):
	extension = os.path.splitext(source_path)[1].lower()
	if extension in AUDIO_EXTENSIONS:
		profile_dir, cleanup_root = _stage_profile_from_audio(source_path)
	elif extension in CONDITIONING_EXTENSIONS:
		profile_dir, cleanup_root = _stage_profile_from_conditioning(source_path)
	elif extension == ".zip":
		cleanup_root = _extract_archive(source_path)
		profile_dir = _find_profile_root(cleanup_root, source_path)
	else:
		raise VoiceStoreError("Unsupported XTTS voice file type: %s" % extension)
	record = _profile_payload_from_dir(profile_dir)
	metadata = dict(record.metadata or {})
	metadata.update(
		create_voice_metadata(
			record.voice_id,
			source_type=source_type,
			source_path=source_path,
			reference_files=[os.path.basename(path) for path in record.reference_paths],
			install_note=install_note,
			extra=dict(extra_metadata or {}, **({"conditioningFile": os.path.basename(record.conditioning_path)} if record.conditioning_path else {})),
		)
	)
	metadata.setdefault("displayName", record.display_name)
	_write_json(os.path.join(profile_dir, PROFILE_FILE_NAME), metadata)
	if validate_conditioning is not None and record.conditioning_path:
		validate_conditioning(record.conditioning_path)
	return profile_dir, metadata, cleanup_root


def install_voice_files(source_path, source_type, overwrite=False, install_note=None, extra_metadata=None, validate_conditioning=None):
	profile_dir, metadata, cleanup_root = _prepare_staged_profile(
		source_path,
		source_type=source_type,
		install_note=install_note,
		extra_metadata=extra_metadata,
		validate_conditioning=validate_conditioning,
	)
	voice_id = metadata["voiceId"]
	target_dir = get_user_voice_profile_dir(voice_id)
	if os.path.isdir(target_dir) and not overwrite:
		shutil.rmtree(cleanup_root, ignore_errors=True)
		raise DuplicateVoiceError("Voice already installed: %s" % voice_id)
	os.makedirs(os.path.dirname(target_dir), exist_ok=True)
	_copy_tree_atomic(profile_dir, target_dir)
	shutil.rmtree(cleanup_root, ignore_errors=True)
	return [
		_build_voice_record(target_dir, "user", get_user_voice_dir(create=True))
	]


def remove_user_voice(voice_id):
	voice_id = _normalize_voice_id(voice_id)
	target_dir = get_user_voice_profile_dir(voice_id)
	if not os.path.isdir(target_dir):
		raise VoiceStoreError("User voice not found: %s" % voice_id)
	removed = []
	for root, __, files in os.walk(target_dir):
		for filename in files:
			removed.append(os.path.join(root, filename))
	shutil.rmtree(target_dir)
	removed.append(target_dir)
	return removed


def resolve_preview_reference_paths(source_path):
	if not source_path:
		raise VoiceStoreError("Missing preview voice source")
	if os.path.isfile(source_path) and _is_audio_file(source_path):
		return [source_path], None
	if os.path.isfile(source_path) and _is_conditioning_file(source_path):
		raise VoiceStoreError("XTTS conditioning files cannot be previewed directly before installation")
	if os.path.isdir(source_path):
		record = _profile_payload_from_dir(source_path)
		if not record.reference_paths:
			raise VoiceStoreError("This XTTS profile has no reference audio available for direct preview")
		return record.reference_paths, None
	if os.path.isfile(source_path) and os.path.basename(source_path).lower() == PROFILE_FILE_NAME:
		record = _profile_payload_from_dir(os.path.dirname(source_path))
		if not record.reference_paths:
			raise VoiceStoreError("This XTTS profile has no reference audio available for direct preview")
		return record.reference_paths, None
	if os.path.isfile(source_path) and os.path.splitext(source_path)[1].lower() == ".zip":
		extracted_root = _extract_archive(source_path)
		profile_dir = _find_profile_root(extracted_root, source_path)
		record = _profile_payload_from_dir(profile_dir)
		if not record.reference_paths:
			shutil.rmtree(extracted_root, ignore_errors=True)
			raise VoiceStoreError("This XTTS archive has no reference audio available for direct preview")
		return record.reference_paths, extracted_root
	raise VoiceStoreError("Unsupported preview source: %s" % source_path)
