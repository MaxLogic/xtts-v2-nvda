# coding: utf-8

import logging
import os
import subprocess

try:
	from logHandler import log
except ImportError:
	log = logging.getLogger(__name__)


ADDON_ID = "maxlogicXTTSv2"


def _addon_root():
	return os.path.abspath(os.path.dirname(__file__))


def _user_data_root():
	return os.path.join(os.environ.get("APPDATA", ""), "nvda", ADDON_ID)


def _bootstrap_script():
	return os.path.join(_addon_root(), "bootstrap-helper-env.ps1")


def _run_bootstrap():
	script = _bootstrap_script()
	if not os.path.isfile(script):
		return False
	command = [
		"powershell.exe",
		"-NoProfile",
		"-ExecutionPolicy",
		"Bypass",
		"-File",
		script,
	]
	log.info("Installing MaxLogic XTTS v2 helper runtime during add-on install.")
	result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
	if result.returncode != 0:
		log.warning("MaxLogic XTTS v2 bootstrap returned %s: %s", result.returncode, result.stderr.strip())
		return False
	log.info("MaxLogic XTTS v2 bootstrap complete: %s", result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no output")
	return True


def onInstall():
	log.info("Installing MaxLogic XTTS v2")
	_run_bootstrap()
