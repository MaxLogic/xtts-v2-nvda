import hashlib
import json
import logging
import os
import tempfile
import urllib.parse
import urllib.request

try:
	from ._paths import (
		get_cache_dir,
		get_community_mirror_voice_dir,
		get_packaged_community_mirror_voice_dir,
		get_temp_dir,
	)
	from ._voice_store import install_voice_files
except ImportError:
	from _paths import (
		get_cache_dir,
		get_community_mirror_voice_dir,
		get_packaged_community_mirror_voice_dir,
		get_temp_dir,
	)
	from _voice_store import install_voice_files

try:
	from logHandler import log
except ImportError:
	log = logging.getLogger(__name__)


CATALOG_SCHEMA_VERSION = 1
CATALOG_CACHE_TTL_SECONDS = 24 * 60 * 60
CATALOGS = {
	"official": {
		"bundleFile": "catalog.json",
		"cacheFile": "official-profile-catalog.json",
		"onlineIndexUrl": None,
	},
	"community": {
		"bundleFile": "community_catalog.json",
		"cacheFile": "community-profile-catalog.json",
		"onlineIndexUrl": None,
	},
}


def _require_catalog(catalog_name):
	try:
		return CATALOGS[catalog_name]
	except KeyError:
		raise RuntimeError("Unknown catalog: %s" % catalog_name)


def _catalog_bundle_path(catalog_name):
	definition = _require_catalog(catalog_name)
	return os.path.join(os.path.dirname(os.path.abspath(__file__)), definition["bundleFile"])


def _catalog_cache_path(catalog_name):
	definition = _require_catalog(catalog_name)
	return os.path.join(get_cache_dir(create=True), definition["cacheFile"])


def _load_json(path):
	with open(path, "r", encoding="utf-8") as handle:
		return json.load(handle)


def load_bundled_catalog(catalog_name):
	payload = _load_json(_catalog_bundle_path(catalog_name))
	payload["catalog"] = catalog_name
	payload["source"] = "bundled"
	payload["stale"] = False
	return payload


def load_cached_catalog(catalog_name):
	cache_path = _catalog_cache_path(catalog_name)
	if not os.path.isfile(cache_path):
		return None
	payload = _load_json(cache_path)
	if payload.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
		return None
	payload["catalog"] = catalog_name
	payload["source"] = "cache"
	payload["stale"] = False
	return payload


def _write_catalog_cache(catalog_name, payload):
	cache_path = _catalog_cache_path(catalog_name)
	temp_dir = get_temp_dir(create=True)
	with tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=".json") as tmp_handle:
		tmp_path = tmp_handle.name
	with open(tmp_path, "w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2, sort_keys=True)
	os.replace(tmp_path, cache_path)
	return cache_path


def _resolve_mirror_path(download_url):
	if not download_url or not download_url.startswith("mirror://"):
		return None
	parsed = urllib.parse.urlparse(download_url)
	relative_path = (parsed.netloc + parsed.path).lstrip("/\\")
	if not relative_path:
		raise RuntimeError("Invalid mirror URL: %s" % download_url)
	file_name = os.path.basename(relative_path)
	user_path = os.path.join(get_community_mirror_voice_dir(create=True), file_name)
	if os.path.isfile(user_path):
		return user_path
	package_root = os.path.dirname(os.path.abspath(__file__))
	packaged_path = os.path.join(get_packaged_community_mirror_voice_dir(package_root), file_name)
	return packaged_path


def _enrich_entries(entries):
	enriched = []
	for entry in entries:
		item = dict(entry)
		download_url = item.get("downloadUrl")
		mirror_path = _resolve_mirror_path(download_url) if download_url else None
		if mirror_path:
			item["availableOnline"] = os.path.isfile(mirror_path)
			item["mirrorPath"] = mirror_path
			if os.path.isfile(mirror_path):
				item["remoteSizeBytes"] = os.path.getsize(mirror_path)
				item["sizeBytes"] = item.get("sizeBytes") or item["remoteSizeBytes"]
		else:
			item["availableOnline"] = bool(download_url)
		enriched.append(item)
	return enriched


def _normalize_remote_url(url):
	if not url or url.startswith("mirror://"):
		return url
	parts = urllib.parse.urlsplit(url)
	if not parts.scheme or not parts.netloc:
		return url
	path = urllib.parse.quote(parts.path, safe="/%:@")
	query = urllib.parse.quote(parts.query, safe="=&%:@/?")
	fragment = urllib.parse.quote(parts.fragment, safe="=&%:@/?")
	return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def resolve_catalog(catalog_name="official", force_refresh=False):
	del force_refresh
	cached = load_cached_catalog(catalog_name)
	if cached is not None:
		cached["entries"] = _enrich_entries(cached.get("entries", []))
		return cached
	bundled = load_bundled_catalog(catalog_name)
	bundled["entries"] = _enrich_entries(bundled.get("entries", []))
	return bundled


def get_catalog_entries(catalog_name="official", force_refresh=False):
	payload = resolve_catalog(catalog_name=catalog_name, force_refresh=force_refresh)
	entries = []
	for entry in payload.get("entries", []):
		item = dict(entry)
		item["catalog"] = catalog_name
		entries.append(item)
	return entries, payload


def _download_to_temp(url, target_name):
	hasher = hashlib.sha256()
	temp_dir = get_temp_dir(create=True)
	staging_dir = tempfile.mkdtemp(dir=temp_dir)
	temp_path = os.path.join(staging_dir, os.path.basename(target_name))
	try:
		mirror_path = _resolve_mirror_path(url)
		if mirror_path:
			if not os.path.isfile(mirror_path):
				raise RuntimeError("Mirror profile bundle not found: %s" % mirror_path)
			with open(mirror_path, "rb") as source, open(temp_path, "wb") as target:
				while True:
					chunk = source.read(65536)
					if not chunk:
						break
					target.write(chunk)
					hasher.update(chunk)
			return temp_path, hasher.hexdigest()
		request_url = _normalize_remote_url(url)
		with urllib.request.urlopen(request_url, timeout=60) as response, open(temp_path, "wb") as target:
			while True:
				chunk = response.read(65536)
				if not chunk:
					break
				target.write(chunk)
				hasher.update(chunk)
	except Exception:
		if os.path.isfile(temp_path):
			os.remove(temp_path)
		if os.path.isdir(staging_dir):
			os.rmdir(staging_dir)
		raise
	return temp_path, hasher.hexdigest()


def download_catalog_voice_to_temp(entry, force_bad_sha=False):
	url = entry["downloadUrl"]
	target_name = entry.get("sourceFile") or entry.get("fileName") or ("%s.zip" % entry["id"])
	log.info("MaxLogic XTTS v2 downloading preview profile to temp. id=%s url=%s", entry["id"], url)
	temp_path, digest = _download_to_temp(url, target_name)
	expected_sha = entry.get("sha256")
	if force_bad_sha:
		expected_sha = "0" * 64
	if expected_sha and digest.lower() != expected_sha.lower():
		if os.path.isfile(temp_path):
			os.remove(temp_path)
		staging_dir = os.path.dirname(temp_path)
		if os.path.isdir(staging_dir):
			os.rmdir(staging_dir)
		raise RuntimeError("SHA-256 mismatch for %s" % entry["id"])
	return {
		"path": temp_path,
		"sha256": digest,
		"cleanupDir": os.path.dirname(temp_path),
	}


def download_catalog_voice(entry, overwrite=False, force_bad_sha=False):
	url = entry["downloadUrl"]
	target_name = entry.get("sourceFile") or entry.get("fileName") or ("%s.zip" % entry["id"])
	catalog_name = entry.get("catalog", "official")
	if catalog_name == "huggingface":
		install_note = "Installed from Hugging Face XTTS search"
	else:
		install_note = "Installed from curated XTTS profile catalog"
	log.info("MaxLogic XTTS v2 downloading catalog profile. id=%s url=%s", entry["id"], url)
	temp_path, digest = _download_to_temp(url, target_name)
	expected_sha = entry.get("sha256")
	if force_bad_sha:
		expected_sha = "0" * 64
	try:
		if expected_sha and digest.lower() != expected_sha.lower():
			raise RuntimeError("SHA-256 mismatch for %s" % entry["id"])
		extra_metadata = {
			"displayName": entry.get("displayName", entry["id"]),
			"catalogId": entry["id"],
			"catalogName": catalog_name,
			"language": entry.get("language"),
			"languageLabel": entry.get("languageLabel"),
			"gender": entry.get("gender"),
			"genderLabel": entry.get("genderLabel"),
			"downloadUrl": url,
			"sha256": digest,
			"hfModelId": entry.get("hfModelId"),
			"hfRepoUrl": entry.get("hfRepoUrl"),
			"license": entry.get("license"),
			"licenseUrl": entry.get("licenseUrl"),
		}
		if entry.get("installVoiceId"):
			extra_metadata["voiceId"] = entry["installVoiceId"]
		records = install_voice_files(
			temp_path,
			source_type="catalog-download",
			overwrite=overwrite,
			install_note=install_note,
			extra_metadata=extra_metadata,
		)
		if catalog_name in CATALOGS:
			cache_payload = {
				"schemaVersion": CATALOG_SCHEMA_VERSION,
				"entries": _enrich_entries(load_bundled_catalog(catalog_name).get("entries", [])),
			}
			_write_catalog_cache(catalog_name, cache_payload)
		return records
	finally:
		if os.path.isfile(temp_path):
			os.remove(temp_path)
		staging_dir = os.path.dirname(temp_path)
		if os.path.isdir(staging_dir):
			os.rmdir(staging_dir)
