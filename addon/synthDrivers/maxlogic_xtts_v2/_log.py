import logging
import os

try:
	from ._paths import ADDON_ID, get_log_dir as get_addon_log_dir
except ImportError:
	from _paths import ADDON_ID, get_log_dir as get_addon_log_dir


def get_log_dir():
	return get_addon_log_dir(create=True)


def get_helper_log_path():
	return os.path.join(get_log_dir(), "helper.log")


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
		file_handler = logging.FileHandler(log_path, encoding="utf-8")
		file_handler.setFormatter(formatter)
		logger.addHandler(file_handler)
	return log_path
