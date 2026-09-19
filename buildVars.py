# -*- coding: UTF-8 -*-


def _(arg):
	return arg


addon_info = {
	"addon_name": "maxlogicXTTSv2",
	"addon_summary": _("MaxLogic XTTS v2"),
	"addon_description": _("""An XTTS v2 speech synthesizer add-on for NVDA with profile management, previews, and speech caching."""),
	"addon_version": "0.1.7",
	"addon_author": "MaxLogic",
	"addon_url": "https://github.com/MaxLogic/xtts-v2-nvda",
	"addon_sourceURL": "https://github.com/MaxLogic/xtts-v2-nvda",
	"addon_docFileName": "readme.html",
	"addon_minimumNVDAVersion": "2024.1",
	"addon_lastTestedNVDAVersion": "2026.2",
	"addon_changelog": _("""Fix manager closing, load XTTS in the background, preview input recordings, and balance speech style across all reference recordings."""),
	"addon_updateChannel": None,
	"addon_license": "MIT",
	"addon_licenseURL": "https://github.com/MaxLogic/xtts-v2-nvda/blob/main/LICENSE",
}

pythonSources = [
	"addon/installTasks.py",
	"addon/globalPlugins/*/*.py",
	"addon/synthDrivers/*/*.py",
]

i18nSources = pythonSources + ["buildVars.py"]

excludedFiles = []

baseLanguage = "en"

# Tables for the performance and preset tables; toc gives headings ids for in-page links.
markdownExtensions = ["tables", "toc"]
