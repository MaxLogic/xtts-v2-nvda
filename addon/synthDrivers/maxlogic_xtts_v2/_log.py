import logging
import os

try:
	from ._paths import ADDON_ID, get_log_dir as get_addon_log_dir
except ImportError:
	from _paths import ADDON_ID, get_log_dir as get_addon_log_dir


def get_log_dir():
	return get_addon_log_dir(create=True)


MAX_HELPER_LOG_BYTES = 2 * 1024 * 1024


def get_helper_log_path():
	return os.path.join(get_log_dir(), "helper.log")


def get_previous_helper_log_path():
	return os.path.join(get_log_dir(), "helper.old.log")


def _rotate_helper_log(log_path):
	try:
		if os.path.getsize(log_path) > MAX_HELPER_LOG_BYTES:
			os.replace(log_path, get_previous_helper_log_path())
	except OSError:
		# Missing, or another helper has it open. That helper's next start will rotate it.
		pass


def configure_helper_file_logger(logger):
	log_path = get_helper_log_path()
	formatter = logging.Formatter(
		"%(asctime)s [%(levelname)s] %(process)d %(name)s: %(message)s"
	)
	already_present = False
	for handler in logger.handlers:
		if isinstance(handler, logging.FileHandler) and os.path.abspath(handler.baseFilename) == os.path.abspath(log_path):
			already_present = True
			break
	if not already_present:
		_rotate_helper_log(log_path)
		file_handler = logging.FileHandler(log_path, encoding="utf-8")
		file_handler.setFormatter(formatter)
		logger.addHandler(file_handler)
	return log_path
