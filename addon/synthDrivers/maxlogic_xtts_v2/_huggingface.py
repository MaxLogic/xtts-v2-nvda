import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
	from ._voice_store import AUDIO_EXTENSIONS, normalize_voice_id
except ImportError:
	from _voice_store import AUDIO_EXTENSIONS, normalize_voice_id


HF_API_BASE = "https://huggingface.co/api/models"
HF_RESULT_LIMIT = 20

LANGUAGE_LABELS = {
	"ar": "Arabic",
	"cs": "Czech",
	"de": "German",
	"en": "English",
	"es": "Spanish",
	"fr": "French",
	"hu": "Hungarian",
	"it": "Italian",
	"ja": "Japanese",
	"ko": "Korean",
	"nl": "Dutch",
	"pl": "Polish",
	"pt": "Portuguese",
	"ru": "Russian",
	"tr": "Turkish",
	"zh": "Chinese",
	"zh-cn": "Chinese (Simplified)",
}

LANGUAGE_HINTS = [
	("zh-cn", ("zh-cn", "zh_cn")),
	("en", ("en", "en-us", "en_us", "english")),
	("de", ("de", "de-de", "de_de", "german")),
	("es", ("es", "es-es", "es_es", "spanish")),
	("fr", ("fr", "fr-fr", "fr_fr", "french")),
	("it", ("it", "it-it", "it_it", "italian")),
	("ja", ("ja", "ja-jp", "ja_jp", "japanese")),
	("pt", ("pt", "pt-pt", "pt_pt", "pt-br", "pt_br", "portuguese")),
	("pl", ("pl", "pl-pl", "pl_pl", "polish")),
	("ru", ("ru", "ru-ru", "ru_ru", "russian")),
	("tr", ("tr", "tr-tr", "tr_tr", "turkish")),
	("nl", ("nl", "nl-nl", "nl_nl", "dutch")),
	("cs", ("cs", "cs-cz", "cs_cz", "czech")),
	("ar", ("ar", "ar-sa", "ar_sa", "arabic")),
	("hu", ("hu", "hu-hu", "hu_hu", "hungarian")),
	("ko", ("ko", "ko-kr", "ko_kr", "korean")),
]


def _fetch_json(url):
	request = urllib.request.Request(
		url,
		headers={
			"Accept": "application/json",
			"User-Agent": "MaxLogicXTTSv2NVDA/0.1",
		},
	)
	with urllib.request.urlopen(request, timeout=30) as response:
		return json.load(response)


def _search_url(query, limit):
	params = urllib.parse.urlencode(
		{
			"search": query,
			"limit": str(limit),
			"full": "true",
			"cardData": "true",
		}
	)
	return "%s?%s" % (HF_API_BASE, params)


def _is_xtts_candidate(model):
	tags = set(model.get("tags") or [])
	card = model.get("cardData") or {}
	base_model = str(card.get("base_model") or "").strip().lower()
	model_id = str(model.get("id") or model.get("modelId") or "").strip().lower()
	if "base_model:coqui/xtts-v2" in {tag.lower() for tag in tags}:
		return True
	if base_model == "coqui/xtts-v2":
		return True
	return "xtts" in model_id


def _is_audio_file(path):
	parsed = urllib.parse.urlsplit(path)
	lower = parsed.path.lower()
	return any(lower.endswith(extension) for extension in AUDIO_EXTENSIONS)


def _build_resolve_url(model_id, relative_path):
	model_part = urllib.parse.quote(model_id, safe="/")
	path_part = urllib.parse.quote(relative_path, safe="/")
	return "https://huggingface.co/%s/resolve/main/%s" % (model_part, path_part)


def _guess_language(path_or_url, card_languages):
	value = urllib.parse.unquote(path_or_url or "").lower()
	normalized = re.sub(r"[^a-z0-9_-]+", " ", value)
	for key, hints in LANGUAGE_HINTS:
		for hint in hints:
			pattern = r"(^|[\s/_-])%s($|[\s/_-])" % re.escape(hint)
			if re.search(pattern, normalized):
				return key
	if len(card_languages) == 1:
		candidate = card_languages[0].lower()
		if candidate in LANGUAGE_LABELS:
			return candidate
	return ""


def _language_label(key):
	return LANGUAGE_LABELS.get(key or "", key or "Unknown language")


def _license_label(model):
	card = model.get("cardData") or {}
	return card.get("license_name") or card.get("license") or "unknown"


def _license_url(model):
	card = model.get("cardData") or {}
	return card.get("license_link") or ""


def _widget_audio_candidates(model):
	card = model.get("cardData") or {}
	widgets = card.get("widget") or []
	for index, widget in enumerate(widgets):
		output = widget.get("output") or {}
		url = (output.get("url") or "").strip()
		if not url or not _is_audio_file(url):
			continue
		yield {
			"url": url,
			"samplePath": urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(url).path)),
			"sampleLabel": "widget_%02d" % index,
			"widgetText": widget.get("text") or "",
		}


def _sample_score(path):
	lower = path.lower()
	score = 0
	if "/samples/" in lower or lower.startswith("samples/"):
		score += 100
	base = os.path.basename(lower)
	for token, value in (
		("sample", 80),
		("reference", 70),
		("prompt", 60),
		("speaker", 60),
		("voice", 50),
		("demo", 40),
	):
		if token in base:
			score += value
	return score


def _sibling_audio_candidates(model):
	candidates = []
	for sibling in model.get("siblings") or []:
		path = sibling.get("rfilename") or ""
		if not path or not _is_audio_file(path):
			continue
		candidates.append((path, _sample_score(path)))
	candidates.sort(key=lambda item: (-item[1], item[0].lower()))
	if not candidates:
		return []
	best = [path for path, score in candidates if score > 0]
	if best:
		return best[:4]
	if len(candidates) <= 2:
		return [path for path, __ in candidates]
	return []


def _safe_install_voice_id(model_id, sample_name, entry_id):
	model_prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", (model_id or "").replace("/", "_")).strip("_")
	candidate = "%s_%s" % (model_prefix, os.path.splitext(sample_name)[0])
	try:
		return normalize_voice_id(candidate)
	except Exception:
		return "hf_" + hashlib.sha1(entry_id.encode("utf-8")).hexdigest()[:12]


def _entry_display_name(model_id, sample_path, total_samples):
	sample_stem = os.path.splitext(os.path.basename(sample_path))[0].replace("_", " ").replace("-", " ").strip()
	if total_samples <= 1:
		return model_id
	return "%s / %s" % (model_id, sample_stem or os.path.basename(sample_path))


def _build_entry(model, sample_url, sample_path, widget_text, total_samples):
	model_id = model.get("id") or model.get("modelId")
	card = model.get("cardData") or {}
	card_languages = [
		str(item).strip().lower()
		for item in (card.get("language") or [])
		if str(item).strip() and str(item).strip().lower() != "multilingual"
	]
	language = _guess_language(sample_path or sample_url, card_languages)
	entry_id = "hf_" + hashlib.sha1(("%s|%s" % (model_id, sample_url)).encode("utf-8")).hexdigest()[:16]
	source_file = urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(sample_url).path))
	return {
		"id": entry_id,
		"catalog": "huggingface",
		"displayName": _entry_display_name(model_id, sample_path or source_file, total_samples),
		"downloadUrl": sample_url,
		"sourceFile": source_file,
		"availableOnline": True,
		"language": language,
		"languageLabel": _language_label(language),
		"gender": "unknown",
		"genderLabel": "Unknown",
		"license": _license_label(model),
		"licenseUrl": _license_url(model),
		"downloads": int(model.get("downloads") or 0),
		"likes": int(model.get("likes") or 0),
		"author": model.get("author") or "",
		"hfModelId": model_id,
		"hfRepoUrl": "https://huggingface.co/%s" % urllib.parse.quote(model_id, safe="/"),
		"hfSamplePath": sample_path or source_file,
		"hfWidgetText": widget_text or "",
		"installVoiceId": _safe_install_voice_id(model_id, source_file, entry_id),
	}


def _entries_from_model(model):
	model_id = model.get("id") or model.get("modelId")
	if not model_id or model.get("private") or model.get("gated"):
		return []
	if not _is_xtts_candidate(model):
		return []
	entries = []
	seen = set()
	widget_candidates = list(_widget_audio_candidates(model))
	if widget_candidates:
		total_samples = len(widget_candidates)
		for candidate in widget_candidates:
			key = candidate["url"]
			if key in seen:
				continue
			seen.add(key)
			entries.append(
				_build_entry(
					model,
					candidate["url"],
					candidate["samplePath"],
					candidate.get("widgetText"),
					total_samples,
				)
			)
		return entries
	sibling_candidates = _sibling_audio_candidates(model)
	total_samples = len(sibling_candidates)
	for sample_path in sibling_candidates:
		url = _build_resolve_url(model_id, sample_path)
		if url in seen:
			continue
		seen.add(url)
		entries.append(_build_entry(model, url, sample_path, "", total_samples))
	return entries


def search_huggingface_entries(query, limit=HF_RESULT_LIMIT):
	query = (query or "").strip() or "xtts"
	limit = max(1, min(int(limit or HF_RESULT_LIMIT), 50))
	payload = _fetch_json(_search_url(query, limit))
	entries = []
	for model in payload:
		entries.extend(_entries_from_model(model))
	entries.sort(key=lambda item: (-int(item.get("likes") or 0), -int(item.get("downloads") or 0), item["displayName"].lower()))
	return entries, {
		"query": query,
		"limit": limit,
		"fetchedAt": datetime.now(timezone.utc).isoformat(),
		"source": "huggingface",
		"count": len(entries),
	}
