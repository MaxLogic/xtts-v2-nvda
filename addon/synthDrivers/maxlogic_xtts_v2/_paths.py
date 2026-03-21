import os


ADDON_ID = "maxlogicXTTSv2"


def get_user_data_dir(create=False):
	base_dir = os.path.join(os.environ.get("APPDATA", ""), "nvda", ADDON_ID)
	if create:
		os.makedirs(base_dir, exist_ok=True)
	return base_dir


def get_user_voice_dir(create=False):
	path = os.path.join(get_user_data_dir(create=create), "voices")
	if create:
		os.makedirs(path, exist_ok=True)
	return path


def get_cache_dir(create=False):
	path = os.path.join(get_user_data_dir(create=create), "cache")
	if create:
		os.makedirs(path, exist_ok=True)
	return path


def get_community_mirror_dir(create=False):
	path = os.path.join(get_user_data_dir(create=create), "community-mirror")
	if create:
		os.makedirs(path, exist_ok=True)
	return path


def get_community_mirror_voice_dir(create=False):
	path = os.path.join(get_community_mirror_dir(create=create), "voices")
	if create:
		os.makedirs(path, exist_ok=True)
	return path


def get_packaged_community_mirror_dir(package_root):
	return os.path.join(package_root, "community_mirror")


def get_packaged_community_mirror_voice_dir(package_root):
	return os.path.join(get_packaged_community_mirror_dir(package_root), "voices")


def get_log_dir(create=False):
	path = os.path.join(get_user_data_dir(create=create), "logs")
	if create:
		os.makedirs(path, exist_ok=True)
	return path


def get_temp_dir(create=False):
	path = os.path.join(get_user_data_dir(create=create), "tmp")
	if create:
		os.makedirs(path, exist_ok=True)
	return path
