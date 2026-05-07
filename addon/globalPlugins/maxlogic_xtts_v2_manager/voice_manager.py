# coding: utf-8

from datetime import datetime
import os
import threading
import time

import gui
from logHandler import log
import ui
import wx
try:
	import wx.media as wxmedia
except Exception:
	wxmedia = None

from . import service


GENDER_FILTERS = [
	("all", _("All genders")),
	("female", _("Female")),
	("male", _("Male")),
	("unknown", _("Unknown")),
]

def _format_catalog_hint(payload):
	source = payload.get("source", "unknown")
	stale = payload.get("stale")
	fetched_at = payload.get("fetchedAt")
	if fetched_at:
		try:
			fetched_label = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M UTC")
		except ValueError:
			fetched_label = fetched_at
	else:
		fetched_label = _("unknown time")
	if source == "network":
		return _("Catalog refreshed from network at {time}").format(time=fetched_label)
	if source == "cache":
		if stale:
			return _("Cached catalog from {time} (stale offline fallback)").format(time=fetched_label)
		return _("Cached catalog from {time} (fresh)").format(time=fetched_label)
	if source == "bundled":
		if stale:
			return _("Bundled catalog fallback in use")
		return _("Bundled catalog")
	return _("Catalog source: {source}").format(source=source)


def _format_voice_source(record):
	source_labels = {
		"package": _("Built-in"),
		"override": _("Override asset root"),
		"reference": _("Reference add-on"),
		"user": _("User-installed"),
	}
	source_label = source_labels.get(record.source, record.source.title())
	name = record.display_name
	metadata = record.metadata or {}
	model_version = metadata.get("modelVersion")
	language_label = metadata.get("languageLabel") or metadata.get("language") or _("Unknown language")
	gender_label = metadata.get("genderLabel") or metadata.get("gender") or _("Unknown gender")
	if model_version:
		name = _("{name} [{model}]").format(name=name, model=model_version)
	return _("{name} | {language} | {gender} | {source}").format(
		name=name,
		language=language_label,
		gender=gender_label,
		source=source_label,
	)


def _format_count_hint(visible_count, total_count, selected_count):
	return _("Showing {visible} of {total} voices | Selected: {selected}").format(
		visible=visible_count,
		total=total_count,
		selected=selected_count,
	)


def _format_huggingface_hint(payload):
	query = payload.get("query") or _("xtts")
	count = int(payload.get("count") or 0)
	fetched_at = payload.get("fetchedAt")
	if fetched_at:
		try:
			fetched_label = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
		except Exception:
			fetched_label = fetched_at
	else:
		fetched_label = _("unknown time")
	return _("Hugging Face search for '{query}' returned {count} playable XTTS results at {time}.").format(
		query=query,
		count=count,
		time=fetched_label,
	)


def _format_cache_size(size_bytes):
	if not size_bytes:
		return _("0 MB")
	return _("{size:.2f} MB").format(size=(size_bytes / float(1024 * 1024)))


def _format_timecode(ms):
	ms = max(0, int(round(float(ms or 0))))
	hours, remainder = divmod(ms, 3600000)
	minutes, remainder = divmod(remainder, 60000)
	seconds, millis = divmod(remainder, 1000)
	return "%02d:%02d:%02d.%03d" % (hours, minutes, seconds, millis)


def _parse_timecode(value):
	text = (value or "").strip()
	if not text:
		raise ValueError(_("Enter a time value."))
	if ":" not in text:
		seconds = float(text)
		if seconds < 0:
			raise ValueError(_("Time values cannot be negative."))
		return int(round(seconds * 1000.0))
	parts = text.split(":")
	if len(parts) > 3:
		raise ValueError(_("Use hh:mm:ss, mm:ss, or seconds."))
	try:
		parts = [float(part) for part in parts]
	except ValueError:
		raise ValueError(_("Use hh:mm:ss, mm:ss, or seconds."))
	if len(parts) == 2:
		minutes, seconds = parts
		hours = 0.0
	else:
		hours, minutes, seconds = parts
	if min(hours, minutes, seconds) < 0:
		raise ValueError(_("Time values cannot be negative."))
	total_seconds = (hours * 3600.0) + (minutes * 60.0) + seconds
	return int(round(total_seconds * 1000.0))


def _preview_language_options(auto_label):
	return [("", auto_label)] + service.get_preview_language_options()


class InstalledVoicesPanel(wx.Panel):
	def __init__(self, parent, on_change):
		super(InstalledVoicesPanel, self).__init__(parent)
		self._on_change = on_change
		self._user_voices = []
		self._builtin_voices = []
		self._setup_busy = None
		self._preview_in_progress = False
		self._preview_playing = False
		self._preview_request_id = 0
		self._refresh_generation = 0
		sizer = wx.BoxSizer(wx.VERTICAL)
		setup_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("Getting started"))
		self.setup_status = wx.StaticText(self, label=_("Loading installed voices..."))
		self.setup_button = wx.Button(self, label=_("Set up XTTS runtime"))
		setup_box.Add(self.setup_status, 0, wx.ALL, 5)
		setup_box.Add(self.setup_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		sizer.Add(setup_box, 0, wx.EXPAND | wx.ALL, 5)
		sizer.Add(wx.StaticText(self, label=_("User-installed voice profiles")), 0, wx.ALL, 5)
		self.voice_list = wx.ListBox(self)
		sizer.Add(self.voice_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.empty_user_hint = wx.StaticText(
			self,
			label=_("No user-installed profiles yet. Packaged profiles remain available below."),
		)
		sizer.Add(self.empty_user_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		sizer.Add(wx.StaticText(self, label=_("Packaged and fallback profiles")), 0, wx.ALL, 5)
		self.builtin_list = wx.ListBox(self, style=wx.LB_SINGLE)
		self.builtin_list.Enable(False)
		sizer.Add(self.builtin_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		preview_row = wx.BoxSizer(wx.HORIZONTAL)
		self._preview_language_options = _preview_language_options(_("Auto (voice default)"))
		preview_row.Add(wx.StaticText(self, label=_("Preview language")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.preview_language_choice = wx.Choice(
			self,
			choices=[label for __, label in self._preview_language_options],
		)
		self.preview_language_choice.SetSelection(0)
		preview_row.Add(self.preview_language_choice, 0, wx.ALL, 5)
		sizer.Add(preview_row, 0, wx.LEFT | wx.RIGHT, 0)
		button_row = wx.BoxSizer(wx.HORIZONTAL)
		self.install_button = wx.Button(self, label=_("Install from local file"))
		self.remove_button = wx.Button(self, label=_("Remove selected voice"))
		self.preview_button = wx.Button(self, label=_("Play sample"))
		self.refresh_button = wx.Button(self, label=_("Refresh installed profiles"))
		button_row.Add(self.install_button, 0, wx.ALL, 5)
		button_row.Add(self.remove_button, 0, wx.ALL, 5)
		button_row.Add(self.preview_button, 0, wx.ALL, 5)
		button_row.Add(self.refresh_button, 0, wx.ALL, 5)
		sizer.Add(button_row, 0, wx.ALL, 0)
		self.SetSizer(sizer)
		self.Bind(wx.EVT_BUTTON, self.on_install, self.install_button)
		self.Bind(wx.EVT_BUTTON, self.on_remove, self.remove_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_sample, self.preview_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_entries(), self.refresh_button)
		self.Bind(wx.EVT_BUTTON, self.on_setup_runtime, self.setup_button)
		self.Bind(wx.EVT_LISTBOX, self.on_select_user_voice, self.voice_list)
		self.Bind(wx.EVT_LISTBOX, self.on_select_builtin_voice, self.builtin_list)
		service.prepare_preview_runtime_async()
		self.refresh_entries()

	def _update_preview_button_state(self, is_loading=False):
		self.preview_button.SetLabel(_("Stop") if self._preview_playing else _("Play sample"))
		can_start = (not is_loading) and self._selected_record() is not None and not self._preview_in_progress
		self.preview_button.Enable(self._preview_playing or can_start)

	def _update_preview_lock_state(self, is_loading=False):
		locked = self._preview_in_progress or is_loading
		self.voice_list.Enable(not locked)
		self.builtin_list.Enable(not locked)
		self.install_button.Enable(not locked)
		self.refresh_button.Enable(not locked)
		self.setup_button.Enable((not locked) and self._setup_busy is None)
		self.remove_button.Enable((not locked) and bool(self._user_voices))

	def _set_loading_state(self, is_loading, message=None):
		self.preview_language_choice.Enable(not is_loading and not self._preview_in_progress)
		if is_loading and message:
			self.setup_status.SetLabel(message)
		self._update_preview_lock_state(is_loading=is_loading)
		self._update_preview_button_state(is_loading=is_loading)
		self.Layout()

	def _apply_inventory(self, inventory, setup_status):
		self._user_voices = inventory["user"]
		self._builtin_voices = inventory["builtin"]
		self.voice_list.SetItems([_format_voice_source(record) for record in self._user_voices])
		self.builtin_list.SetItems([_format_voice_source(record) for record in self._builtin_voices])
		self.remove_button.Enable(bool(self._user_voices))
		self.empty_user_hint.SetLabel(_("No user-installed profiles yet. Packaged profiles remain available below."))
		self.empty_user_hint.Show(not self._user_voices)
		self.setup_status.SetLabel(setup_status["message"])
		if self._selected_record() is None:
			self.voice_list.SetSelection(wx.NOT_FOUND)
			self.builtin_list.SetSelection(wx.NOT_FOUND)
		self._set_loading_state(False)
		self.Layout()

	def _finish_refresh(self, generation, inventory=None, setup_status=None, error_message=None):
		if generation != self._refresh_generation:
			return
		if error_message:
			log.exception("MaxLogic XTTS v2 installed voices refresh failed")
			self.setup_status.SetLabel(_("Unable to load installed voices right now."))
			self._set_loading_state(False)
			return
		self._apply_inventory(inventory, setup_status)

	def refresh_entries(self):
		self._refresh_generation += 1
		generation = self._refresh_generation
		self._set_loading_state(True, _("Loading installed voices..."))

		def _worker():
			try:
				inventory = service.list_voice_inventory()
				setup_status = service.get_setup_status()
			except Exception as error:
				wx.CallAfter(self._finish_refresh, generation, None, None, str(error))
				return
			wx.CallAfter(self._finish_refresh, generation, inventory, setup_status, None)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2InstalledLoad", daemon=True)
		thread.start()

	def _selected_record(self):
		index = self.voice_list.GetSelection()
		if index != wx.NOT_FOUND and index < len(self._user_voices):
			return self._user_voices[index]
		index = self.builtin_list.GetSelection()
		if index != wx.NOT_FOUND and index < len(self._builtin_voices):
			return self._builtin_voices[index]
		return None

	def _selected_preview_language(self):
		index = self.preview_language_choice.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._preview_language_options):
			return None
		return self._preview_language_options[index][0] or None

	def on_select_user_voice(self, event):
		if self.voice_list.GetSelection() != wx.NOT_FOUND:
			self.builtin_list.SetSelection(wx.NOT_FOUND)
		self._update_preview_button_state()
		event.Skip()

	def on_select_builtin_voice(self, event):
		if self.builtin_list.GetSelection() != wx.NOT_FOUND:
			self.voice_list.SetSelection(wx.NOT_FOUND)
		self._update_preview_button_state()
		event.Skip()

	def _run_busy(self, message, callback):
		busy = wx.BusyInfo(message, parent=self)
		try:
			return callback()
		finally:
			del busy

	def _finish_setup(self, result=None, error_message=None):
		if self._setup_busy is not None:
			del self._setup_busy
			self._setup_busy = None
		self.setup_button.Enable(True)
		self.refresh_entries()
		self._on_change()
		if error_message:
			gui.messageBox(
				_("XTTS setup failed.\nSee NVDA's log for details.\n{error}").format(error=error_message),
				_("XTTS setup failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		message = _("XTTS runtime is ready.")
		service.prepare_preview_runtime_async()
		gui.messageBox(message, _("XTTS setup complete"), wx.OK | wx.ICON_INFORMATION)

	def on_setup_runtime(self, event):
		if self._setup_busy is not None:
			return
		self.setup_button.Enable(False)
		self._setup_busy = wx.BusyInfo(_("Setting up XTTS runtime..."), parent=self)

		def _worker():
			try:
				result = service.run_runtime_setup()
			except Exception as error:
				log.exception("MaxLogic XTTS v2 runtime setup failed", exc_info=True)
				wx.CallAfter(self._finish_setup, None, str(error))
				return
			if not result.get("ok"):
				error_message = result.get("stderr") or result.get("stdout") or _("Unknown setup error")
				log.warning("MaxLogic XTTS v2 runtime setup returned failure: %s", error_message)
				wx.CallAfter(self._finish_setup, None, error_message)
				return
			wx.CallAfter(self._finish_setup, result, None)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2Setup", daemon=True)
		thread.start()

	def on_install(self, event):
		dialog = wx.FileDialog(
			parent=gui.mainFrame,
			message=_("Choose XTTS reference audio, a conditioning file, or a profile bundle"),
			wildcard="XTTS profile files (*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac;*.pth;*.zip)|*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac;*.pth;*.zip",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		gui.mainFrame.prePopup()
		try:
			result_code = dialog.ShowModal()
		finally:
			gui.mainFrame.postPopup()
		if result_code != wx.ID_OK:
			return
		source_path = dialog.GetPath().strip()
		if not source_path:
			return
		try:
			result = self._run_busy(
				_("Installing local voice..."),
				lambda: service.install_local_voice(source_path, overwrite=False),
			)
		except service.DuplicateVoiceError:
			overwrite = gui.messageBox(
				_("This voice is already installed. Do you want to overwrite the user-managed copy?"),
				_("Voice already installed"),
				wx.YES_NO | wx.ICON_WARNING,
			)
			if overwrite != wx.YES:
				return
			result = self._run_busy(
				_("Overwriting local voice..."),
				lambda: service.install_local_voice(source_path, overwrite=True),
			)
		except Exception as error:
			log.exception("MaxLogic XTTS v2 local install failed", exc_info=True)
			gui.messageBox(
				_("Voice installation failed.\nSee NVDA's log for details.\n{error}").format(error=error),
				_("Voice installation failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self.refresh_entries()
		self._on_change()
		refresh = result["refresh"]
		message = _("Installed voice successfully.")
		if refresh.get("restartRequired"):
			message += "\n" + _("Restart NVDA to refresh the current synth.")
		gui.messageBox(message, _("Voice installed"), wx.OK | wx.ICON_INFORMATION)

	def on_remove(self, event):
		record = self._selected_record()
		if record is None or record.source != "user":
			return
		response = gui.messageBox(
			_("Do you want to remove this user-installed voice?\nVoice: {voice}").format(voice=record.display_name),
			_("Remove voice?"),
			wx.YES_NO | wx.ICON_WARNING,
		)
		if response != wx.YES:
			return
		try:
			result = self._run_busy(
				_("Removing local voice..."),
				lambda: service.remove_local_voice(record.voice_id),
			)
		except Exception as error:
			log.exception("MaxLogic XTTS v2 local remove failed", exc_info=True)
			gui.messageBox(
				_("Voice removal failed.\nSee NVDA's log for details.\n{error}").format(error=error),
				_("Voice removal failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self.refresh_entries()
		self._on_change()
		message = _("Removed voice successfully.")
		if result["refresh"].get("restartRequired"):
			message += "\n" + _("Restart NVDA to refresh the current synth.")
		gui.messageBox(message, _("Voice removed"), wx.OK | wx.ICON_INFORMATION)

	def on_play_sample(self, event):
		if self._preview_playing:
			self._preview_request_id += 1
			service.stop_preview()
			self._preview_in_progress = False
			self._preview_playing = False
			self.preview_language_choice.Enable(True)
			self.setup_status.SetLabel(service.get_setup_status()["message"])
			self._update_preview_lock_state()
			self._update_preview_button_state()
			return
		record = self._selected_record()
		if record is None:
			gui.messageBox(
				_("Select one installed voice in the list to play a sample."),
				_("No voice selected"),
				wx.OK | wx.ICON_INFORMATION,
			)
			return
		self._preview_in_progress = True
		self._preview_playing = False
		self._preview_request_id += 1
		request_id = self._preview_request_id
		self._update_preview_lock_state()
		self._update_preview_button_state()
		self.preview_language_choice.Enable(False)
		status_message = _("Generating sample for {name}. The first preview can take around a minute.").format(
			name=record.display_name,
		)
		self.setup_status.SetLabel(status_message)
		ui.message(status_message)

		def _on_started():
			if request_id != self._preview_request_id:
				return
			self._preview_playing = True
			self._update_preview_lock_state()
			self._update_preview_button_state()
			self.setup_status.SetLabel(_("Playing sample for {name}.").format(name=record.display_name))

		def _on_complete(status, error_message):
			if request_id != self._preview_request_id:
				return
			self._preview_in_progress = False
			self._preview_playing = False
			self._update_preview_lock_state()
			self._update_preview_button_state()
			self.preview_language_choice.Enable(True)
			if status in ("superseded", "stopped"):
				self.setup_status.SetLabel(service.get_setup_status()["message"])
				return
			if error_message:
				self.setup_status.SetLabel(_("Sample playback failed."))
				gui.messageBox(
					_("Sample playback failed.\nSee NVDA's log for details.\n{error}").format(error=error_message),
					_("Sample playback failed"),
					wx.OK | wx.ICON_ERROR,
				)
				return
			self.setup_status.SetLabel(service.get_setup_status()["message"])

		service.play_installed_voice_sample(
			record,
			on_complete=_on_complete,
			preview_language=self._selected_preview_language(),
			on_playback_started=_on_started,
		)


class CatalogVoicesPanel(wx.Panel):
	def __init__(self, parent, on_change, catalog_name, title, empty_message, allow_refresh, show_hide_local_toggle=False):
		super(CatalogVoicesPanel, self).__init__(parent)
		self._on_change = on_change
		self._catalog_name = catalog_name
		self._title = title
		self._empty_message = empty_message
		self._allow_refresh = allow_refresh
		self._show_hide_local_toggle = show_hide_local_toggle
		self._entries = []
		self._visible_entries = []
		self._installed_voice_ids = set()
		self._checked_ids = set()
		self._language_options = [("", _("All languages"))]
		self._preview_in_progress = False
		self._preview_playing = False
		self._preview_request_id = 0
		self._refresh_generation = 0
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=title), 0, wx.ALL, 5)

		filter_row = wx.BoxSizer(wx.HORIZONTAL)
		filter_row.Add(wx.StaticText(self, label=_("Filter")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.search_text = wx.TextCtrl(self)
		filter_row.Add(self.search_text, 1, wx.EXPAND | wx.ALL, 5)
		filter_row.Add(wx.StaticText(self, label=_("Gender")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.gender_choice = wx.Choice(self, choices=[label for __, label in GENDER_FILTERS])
		self.gender_choice.SetSelection(0)
		filter_row.Add(self.gender_choice, 0, wx.ALL, 5)
		filter_row.Add(wx.StaticText(self, label=_("Language")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.language_choice = wx.Choice(self, choices=[self._language_options[0][1]])
		self.language_choice.SetSelection(0)
		filter_row.Add(self.language_choice, 0, wx.ALL, 5)
		sizer.Add(filter_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 0)
		if self._show_hide_local_toggle:
			self.hide_installed_checkbox = wx.CheckBox(self, label=_("Hide voices already available locally"))
			sizer.Add(self.hide_installed_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		else:
			self.hide_installed_checkbox = None

		self.voice_list = wx.CheckListBox(self)
		sizer.Add(self.voice_list, 1, wx.EXPAND | wx.ALL, 5)
		self.result_hint = wx.StaticText(self, label="")
		sizer.Add(self.result_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.empty_hint = wx.StaticText(self, label="")
		sizer.Add(self.empty_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.catalog_hint = wx.StaticText(self, label="")
		sizer.Add(self.catalog_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		preview_row = wx.BoxSizer(wx.HORIZONTAL)
		self._preview_language_options = _preview_language_options(_("Auto (voice language)"))
		preview_row.Add(wx.StaticText(self, label=_("Preview language")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.preview_language_choice = wx.Choice(
			self,
			choices=[label for __, label in self._preview_language_options],
		)
		self.preview_language_choice.SetSelection(0)
		preview_row.Add(self.preview_language_choice, 0, wx.ALL, 5)
		sizer.Add(preview_row, 0, wx.LEFT | wx.RIGHT, 0)

		button_row = wx.BoxSizer(wx.HORIZONTAL)
		self.select_button = wx.Button(self, label=_("Select visible"))
		self.clear_button = wx.Button(self, label=_("Clear visible"))
		self.preview_button = wx.Button(self, label=_("Play sample"))
		self.download_button = wx.Button(self, label=_("Download selected voices"))
		self.refresh_button = wx.Button(self, label=_("Refresh catalog"))
		button_row.Add(self.select_button, 0, wx.ALL, 5)
		button_row.Add(self.clear_button, 0, wx.ALL, 5)
		button_row.Add(self.preview_button, 0, wx.ALL, 5)
		button_row.Add(self.download_button, 0, wx.ALL, 5)
		button_row.Add(self.refresh_button, 0, wx.ALL, 5)
		sizer.Add(button_row, 0, wx.ALL, 0)
		self.SetSizer(sizer)

		self.Bind(wx.EVT_TEXT, lambda evt: self._apply_filters(), self.search_text)
		self.Bind(wx.EVT_CHOICE, lambda evt: self._apply_filters(), self.gender_choice)
		self.Bind(wx.EVT_CHOICE, lambda evt: self._apply_filters(), self.language_choice)
		if self.hide_installed_checkbox is not None:
			self.Bind(wx.EVT_CHECKBOX, lambda evt: self._apply_filters(), self.hide_installed_checkbox)
		self.Bind(wx.EVT_LISTBOX, lambda evt: (self._update_detail_hint(), self._update_action_state()), self.voice_list)
		self.Bind(wx.EVT_CHECKLISTBOX, self.on_toggle_entry, self.voice_list)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.on_select_visible(), self.select_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.on_clear_visible(), self.clear_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_sample, self.preview_button)
		self.Bind(wx.EVT_BUTTON, self.on_download_selected, self.download_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_entries(force_refresh=True), self.refresh_button)
		service.prepare_preview_runtime_async()
		self.refresh_entries()

	def _update_preview_button_state(self):
		focused_entry = self._focused_entry()
		self.preview_button.SetLabel(_("Stop") if self._preview_playing else _("Play sample"))
		can_start = focused_entry is not None and focused_entry.get("availableOnline", True) and not self._preview_in_progress
		self.preview_button.Enable(self._preview_playing or can_start)

	def _update_preview_lock_state(self):
		locked = self._preview_in_progress
		self.search_text.Enable(not locked)
		self.gender_choice.Enable(not locked)
		self.language_choice.Enable(not locked)
		if self.hide_installed_checkbox is not None:
			self.hide_installed_checkbox.Enable(not locked)
		self.refresh_button.Enable((not locked) and self._allow_refresh)

	def _format_entry(self, entry):
		size_bytes = entry.get("remoteSizeBytes") or entry.get("sizeBytes") or 0
		size_label = _("{size:.2f} MB").format(size=round(size_bytes / (1024.0 * 1024.0), 2)) if size_bytes else _("size unknown")
		language_label = entry.get("languageLabel") or entry.get("language") or _("Unknown language")
		gender_label = entry.get("genderLabel") or _("Unknown")
		status = _("ready") if entry.get("availableOnline", True) else _("metadata only")
		return _("{name} | {language} | {gender} | {size} | {status}").format(
			name=entry.get("displayName", entry["id"]),
			language=language_label,
			gender=gender_label,
			size=size_label,
			status=status,
		)

	def _selected_gender_key(self):
		index = self.gender_choice.GetSelection()
		if index == wx.NOT_FOUND:
			return "all"
		return GENDER_FILTERS[index][0]

	def _focused_entry(self):
		index = self.voice_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._visible_entries):
			return None
		return self._visible_entries[index]

	def _selected_language_key(self):
		index = self.language_choice.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._language_options):
			return ""
		return self._language_options[index][0]

	def _selected_preview_language(self):
		index = self.preview_language_choice.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._preview_language_options):
			return None
		return self._preview_language_options[index][0] or None

	def _update_language_choices(self):
		current_key = self._selected_language_key()
		options = [("", _("All languages"))]
		seen = set()
		for entry in self._entries:
			key = entry.get("language", "") or ""
			label = entry.get("languageLabel") or key or _("Unknown language")
			if key in seen:
				continue
			seen.add(key)
			options.append((key, label))
		options.sort(key=lambda item: (item[0] != "", item[1].lower()))
		self._language_options = options
		self.language_choice.SetItems([label for __, label in options])
		target_index = 0
		for index, item in enumerate(options):
			if item[0] == current_key:
				target_index = index
				break
		self.language_choice.SetSelection(target_index)

	def _set_loading_state(self, is_loading, message=None):
		self.search_text.Enable(not is_loading)
		self.gender_choice.Enable(not is_loading)
		self.language_choice.Enable(not is_loading)
		self.preview_language_choice.Enable(not is_loading and not self._preview_in_progress)
		if self.hide_installed_checkbox is not None:
			self.hide_installed_checkbox.Enable(not is_loading)
		self.voice_list.Enable(not is_loading)
		self.select_button.Enable(False if is_loading else self.select_button.IsEnabled())
		self.clear_button.Enable(False if is_loading else self.clear_button.IsEnabled())
		if is_loading:
			self.preview_button.Enable(False)
		else:
			self._update_preview_button_state()
		self.download_button.Enable(False if is_loading else self.download_button.IsEnabled())
		self.refresh_button.Enable((not is_loading) and self._allow_refresh)
		if is_loading:
			self.result_hint.SetLabel(message or _("Loading catalog..."))
			self.empty_hint.SetLabel("")
		else:
			self._update_preview_lock_state()
		self.Layout()

	def _apply_refresh_payload(self, entries, payload, inventory):
		self._entries = entries
		self._installed_voice_ids = {record.voice_id for record in inventory["user"]}
		self._installed_voice_ids.update(record.voice_id for record in inventory["builtin"])
		self._entries = sorted(self._entries, key=lambda entry: entry.get("displayName", entry["id"]).lower())
		self._checked_ids.intersection_update({entry["id"] for entry in self._entries})
		self._update_language_choices()
		self.catalog_hint.SetLabel(_format_catalog_hint(payload))
		if not self._allow_refresh:
			self.refresh_button.Enable(False)
			self.refresh_button.Hide()
		self._set_loading_state(False)
		self._apply_filters()

	def _finish_refresh(self, generation, entries=None, payload=None, inventory=None, error_message=None):
		if generation != self._refresh_generation:
			return
		if error_message:
			log.exception("MaxLogic XTTS v2 catalog refresh failed. catalog=%s", self._catalog_name)
			self.catalog_hint.SetLabel(_("Catalog could not be loaded right now."))
			self.result_hint.SetLabel("")
			self.empty_hint.SetLabel(_("Try again in a moment."))
			self._set_loading_state(False)
			return
		self._apply_refresh_payload(entries, payload, inventory)

	def refresh_entries(self, force_refresh=False):
		self._refresh_generation += 1
		generation = self._refresh_generation
		self._set_loading_state(True, _("Loading catalog..."))

		def _worker():
			try:
				entries, payload = service.list_catalog_voices(
					catalog_name=self._catalog_name,
					force_refresh=force_refresh,
				)
				inventory = service.list_voice_inventory()
			except Exception as error:
				wx.CallAfter(self._finish_refresh, generation, None, None, None, str(error))
				return
			wx.CallAfter(self._finish_refresh, generation, entries, payload, inventory, None)

		thread = threading.Thread(
			target=_worker,
			name="MaxLogicXTTSV2CatalogLoad-%s" % self._catalog_name,
			daemon=True,
		)
		thread.start()

	def _apply_filters(self):
		search_text = self.search_text.GetValue().strip().lower()
		gender_key = self._selected_gender_key()
		language_key = self._selected_language_key()
		hide_installed = self.hide_installed_checkbox is not None and self.hide_installed_checkbox.GetValue()
		visible_entries = []
		for entry in self._entries:
			name = entry.get("displayName", entry["id"]).lower()
			if search_text and search_text not in name and search_text not in entry["id"].lower():
				continue
			if hide_installed and entry["id"] in self._installed_voice_ids:
				continue
			if gender_key != "all" and entry.get("gender", "unknown") != gender_key:
				continue
			if language_key and entry.get("language", "") != language_key:
				continue
			visible_entries.append(entry)
		self._visible_entries = visible_entries
		self.voice_list.SetItems([self._format_entry(entry) for entry in visible_entries])
		for index, entry in enumerate(visible_entries):
			self.voice_list.Check(index, entry["id"] in self._checked_ids)
		self.result_hint.SetLabel(
			_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
		)
		if self._entries and not self._visible_entries:
			self.empty_hint.SetLabel(_("No voices match the current filters."))
		elif not self._entries:
			self.empty_hint.SetLabel(self._empty_message)
		else:
			self.empty_hint.SetLabel("")
		self._update_action_state()
		self.Layout()

	def _update_action_state(self):
		has_visible = bool(self._visible_entries)
		self._update_detail_hint()
		self.select_button.Enable(has_visible and not self._preview_in_progress)
		self.clear_button.Enable(has_visible and not self._preview_in_progress)
		self.download_button.Enable(bool(self._checked_ids) and not self._preview_in_progress)
		self._update_preview_button_state()
		self._update_preview_lock_state()

	def _update_detail_hint(self):
		return

	def on_toggle_entry(self, event):
		index = event.GetInt()
		if index == wx.NOT_FOUND or index >= len(self._visible_entries):
			return
		entry = self._visible_entries[index]
		if self.voice_list.IsChecked(index):
			self._checked_ids.add(entry["id"])
		else:
			self._checked_ids.discard(entry["id"])
		self.result_hint.SetLabel(
			_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
		)
		self._update_action_state()

	def on_select_visible(self):
		for entry in self._visible_entries:
			self._checked_ids.add(entry["id"])
		self._apply_filters()

	def on_clear_visible(self):
		for entry in self._visible_entries:
			self._checked_ids.discard(entry["id"])
		self._apply_filters()

	def _run_busy(self, message, callback):
		busy = wx.BusyInfo(message, parent=self)
		try:
			return callback()
		finally:
			del busy

	def _install_entry(self, entry, overwrite):
		return service.install_catalog_voice(entry, overwrite=overwrite, refresh=False)

	def on_play_sample(self, event):
		if self._preview_playing:
			self._preview_request_id += 1
			service.stop_preview()
			self._preview_in_progress = False
			self._preview_playing = False
			self.preview_language_choice.Enable(True)
			self.result_hint.SetLabel(
				_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
			)
			self._update_action_state()
			return
		entry = self._focused_entry()
		if entry is None:
			gui.messageBox(
				_("Select one voice in the list to play a sample."),
				_("No voice selected"),
				wx.OK | wx.ICON_INFORMATION,
			)
			return
		if not entry.get("availableOnline", True):
			gui.messageBox(
				_("This voice is not currently available from the online catalog source."),
				_("Voice unavailable"),
				wx.OK | wx.ICON_WARNING,
			)
			return
		self._preview_in_progress = True
		self._preview_playing = False
		self._preview_request_id += 1
		request_id = self._preview_request_id
		self.preview_language_choice.Enable(False)
		status_message = _("Generating sample for {name}. The first preview can take around a minute.").format(
			name=entry.get("displayName", entry["id"]),
		)
		self.result_hint.SetLabel(status_message)
		ui.message(status_message)
		self._update_action_state()

		def _on_started():
			if request_id != self._preview_request_id:
				return
			self._preview_playing = True
			self.result_hint.SetLabel(_("Playing sample for {name}.").format(name=entry.get("displayName", entry["id"])))
			self._update_action_state()

		def _on_complete(status, error_message):
			if request_id != self._preview_request_id:
				return
			self._preview_in_progress = False
			self._preview_playing = False
			self.preview_language_choice.Enable(True)
			if status in ("superseded", "stopped"):
				self.result_hint.SetLabel(
					_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
				)
				self._update_action_state()
				return
			if error_message:
				self.result_hint.SetLabel(_("Sample playback failed."))
				gui.messageBox(
					_("Sample playback failed.\nSee NVDA's log for details.\n{error}").format(error=error_message),
					_("Sample playback failed"),
					wx.OK | wx.ICON_ERROR,
				)
			else:
				self.result_hint.SetLabel(
					_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
				)
			self._update_action_state()

		service.play_catalog_voice_sample(
			entry,
			on_complete=_on_complete,
			preview_language=self._selected_preview_language(),
			on_playback_started=_on_started,
		)

	def on_download_selected(self, event):
		selected_entries = [entry for entry in self._entries if entry["id"] in self._checked_ids]
		if not selected_entries:
			return
		unavailable = [entry for entry in selected_entries if not entry.get("availableOnline", True)]
		downloadable = [entry for entry in selected_entries if entry.get("availableOnline", True)]
		if not downloadable:
			gui.messageBox(
				_("None of the selected voices are currently available for download."),
				_("No downloadable voices"),
				wx.OK | wx.ICON_WARNING,
			)
			return
		installed = []
		overwritten = []
		duplicates = []
		failures = []

		def _initial_pass():
			for entry in downloadable:
				try:
					result = self._install_entry(entry, overwrite=False)
				except service.DuplicateVoiceError:
					duplicates.append(entry)
				except Exception as error:
					log.exception("MaxLogic XTTS v2 catalog download failed", exc_info=True)
					failures.append((entry, error))
				else:
					installed.extend(result["records"])

		self._run_busy(_("Downloading selected voices..."), _initial_pass)

		if duplicates:
			response = gui.messageBox(
				_("{count} selected voices are already installed. Overwrite the user-managed copies?").format(
					count=len(duplicates)
				),
				_("Overwrite installed voices?"),
				wx.YES_NO | wx.ICON_WARNING,
			)
			if response == wx.YES:
				def _overwrite_pass():
					for entry in duplicates:
						try:
							result = self._install_entry(entry, overwrite=True)
						except Exception as error:
							log.exception("MaxLogic XTTS v2 catalog overwrite failed", exc_info=True)
							failures.append((entry, error))
						else:
							overwritten.extend(result["records"])

				self._run_busy(_("Overwriting selected voices..."), _overwrite_pass)

		refresh = service.refresh_active_synth(
			reason="%s-batch-install" % self._catalog_name,
			preferred_voice=(installed or overwritten)[0].voice_id if (installed or overwritten) else None,
		)
		self._checked_ids.clear()
		self._on_change()

		message_lines = []
		if installed:
			message_lines.append(_("Installed {count} voices.").format(count=len(installed)))
		if overwritten:
			message_lines.append(_("Overwrote {count} voices.").format(count=len(overwritten)))
		if unavailable:
			message_lines.append(_("Skipped {count} unavailable voices.").format(count=len(unavailable)))
		if duplicates and not overwritten:
			message_lines.append(_("Skipped {count} already-installed voices.").format(count=len(duplicates)))
		if failures:
			message_lines.append(_("Failed to install {count} voices. See NVDA's log for details.").format(count=len(failures)))
		if refresh.get("restartRequired"):
			message_lines.append(_("Restart NVDA to refresh the current synth."))
		if not message_lines:
			message_lines.append(_("No voice changes were applied."))
		title = _("Voice download complete") if not failures else _("Voice download completed with issues")
		gui.messageBox("\n".join(message_lines), title, wx.OK | wx.ICON_INFORMATION)


class HuggingFaceSearchPanel(wx.Panel):
	def __init__(self, parent, on_change):
		super(HuggingFaceSearchPanel, self).__init__(parent)
		self._on_change = on_change
		self._entries = []
		self._visible_entries = []
		self._checked_ids = set()
		self._installed_voice_ids = set()
		self._language_options = [("", _("All languages"))]
		self._preview_in_progress = False
		self._preview_playing = False
		self._preview_request_id = 0
		self._search_generation = 0
		self._last_payload = {}
		self._last_query = "xtts"
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(
			wx.StaticText(
				self,
				label=_("Search Hugging Face for XTTS-compatible model repos that include playable sample audio."),
			),
			0,
			wx.ALL,
			5,
		)

		search_row = wx.BoxSizer(wx.HORIZONTAL)
		search_row.Add(wx.StaticText(self, label=_("Query")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.search_text = wx.TextCtrl(self, value="xtts", style=wx.TE_PROCESS_ENTER)
		self.search_button = wx.Button(self, label=_("Search Hugging Face"))
		search_row.Add(self.search_text, 1, wx.EXPAND | wx.ALL, 5)
		search_row.Add(self.search_button, 0, wx.ALL, 5)
		sizer.Add(search_row, 0, wx.EXPAND)

		filter_row = wx.BoxSizer(wx.HORIZONTAL)
		filter_row.Add(wx.StaticText(self, label=_("Gender")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.gender_choice = wx.Choice(self, choices=[label for __, label in GENDER_FILTERS])
		self.gender_choice.SetSelection(0)
		filter_row.Add(self.gender_choice, 0, wx.ALL, 5)
		filter_row.Add(wx.StaticText(self, label=_("Language")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.language_choice = wx.Choice(self, choices=[self._language_options[0][1]])
		self.language_choice.SetSelection(0)
		filter_row.Add(self.language_choice, 0, wx.ALL, 5)
		self.hide_installed_checkbox = wx.CheckBox(self, label=_("Hide voices already available locally"))
		filter_row.Add(self.hide_installed_checkbox, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
		sizer.Add(filter_row, 0, wx.EXPAND)

		self.voice_list = wx.CheckListBox(self)
		sizer.Add(self.voice_list, 1, wx.EXPAND | wx.ALL, 5)

		self.detail_hint = wx.StaticText(self, label="")
		sizer.Add(self.detail_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.result_hint = wx.StaticText(self, label="")
		sizer.Add(self.result_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.empty_hint = wx.StaticText(self, label=_("Press Search Hugging Face to load results."))
		sizer.Add(self.empty_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.search_hint = wx.StaticText(self, label="")
		sizer.Add(self.search_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

		preview_row = wx.BoxSizer(wx.HORIZONTAL)
		self._preview_language_options = _preview_language_options(_("Auto (voice language)"))
		preview_row.Add(wx.StaticText(self, label=_("Preview language")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.preview_language_choice = wx.Choice(
			self,
			choices=[label for __, label in self._preview_language_options],
		)
		self.preview_language_choice.SetSelection(0)
		preview_row.Add(self.preview_language_choice, 0, wx.ALL, 5)
		sizer.Add(preview_row, 0, wx.LEFT | wx.RIGHT, 0)

		button_row = wx.BoxSizer(wx.HORIZONTAL)
		self.select_button = wx.Button(self, label=_("Select visible"))
		self.clear_button = wx.Button(self, label=_("Clear visible"))
		self.preview_button = wx.Button(self, label=_("Play sample"))
		self.install_button = wx.Button(self, label=_("Install selected voices"))
		button_row.Add(self.select_button, 0, wx.ALL, 5)
		button_row.Add(self.clear_button, 0, wx.ALL, 5)
		button_row.Add(self.preview_button, 0, wx.ALL, 5)
		button_row.Add(self.install_button, 0, wx.ALL, 5)
		sizer.Add(button_row, 0, wx.ALL, 0)
		self.SetSizer(sizer)

		self.Bind(wx.EVT_BUTTON, lambda evt: self.search_entries(), self.search_button)
		self.Bind(wx.EVT_TEXT_ENTER, lambda evt: self.search_entries(), self.search_text)
		self.Bind(wx.EVT_CHOICE, lambda evt: self._apply_filters(), self.gender_choice)
		self.Bind(wx.EVT_CHOICE, lambda evt: self._apply_filters(), self.language_choice)
		self.Bind(wx.EVT_CHECKBOX, lambda evt: self._apply_filters(), self.hide_installed_checkbox)
		self.Bind(wx.EVT_LISTBOX, lambda evt: self._update_action_state(), self.voice_list)
		self.Bind(wx.EVT_CHECKLISTBOX, self.on_toggle_entry, self.voice_list)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.on_select_visible(), self.select_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.on_clear_visible(), self.clear_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_sample, self.preview_button)
		self.Bind(wx.EVT_BUTTON, self.on_install_selected, self.install_button)
		service.prepare_preview_runtime_async()
		self.search_entries(initial=True)

	def _update_preview_button_state(self):
		focused_entry = self._focused_entry()
		self.preview_button.SetLabel(_("Stop") if self._preview_playing else _("Play sample"))
		can_start = focused_entry is not None and not self._preview_in_progress
		self.preview_button.Enable(self._preview_playing or can_start)

	def _update_preview_lock_state(self):
		locked = self._preview_in_progress
		self.search_text.Enable(not locked)
		self.search_button.Enable(not locked)
		self.gender_choice.Enable(not locked)
		self.language_choice.Enable(not locked)
		self.hide_installed_checkbox.Enable(not locked)

	def _format_entry(self, entry):
		language_label = entry.get("languageLabel") or _("Unknown language")
		license_label = entry.get("license") or _("unknown")
		downloads = int(entry.get("downloads") or 0)
		return _("{name} | {language} | {license} | {downloads} downloads").format(
			name=entry.get("displayName", entry["id"]),
			language=language_label,
			license=license_label,
			downloads=downloads,
		)

	def _focused_entry(self):
		index = self.voice_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._visible_entries):
			return None
		return self._visible_entries[index]

	def _selected_gender_key(self):
		index = self.gender_choice.GetSelection()
		if index == wx.NOT_FOUND:
			return "all"
		return GENDER_FILTERS[index][0]

	def _selected_language_key(self):
		index = self.language_choice.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._language_options):
			return ""
		return self._language_options[index][0]

	def _selected_preview_language(self):
		index = self.preview_language_choice.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._preview_language_options):
			return None
		return self._preview_language_options[index][0] or None

	def _update_language_choices(self):
		current_key = self._selected_language_key()
		options = [("", _("All languages"))]
		seen = set()
		for entry in self._entries:
			key = entry.get("language", "") or ""
			label = entry.get("languageLabel") or key or _("Unknown language")
			if key in seen:
				continue
			seen.add(key)
			options.append((key, label))
		options.sort(key=lambda item: (item[0] != "", item[1].lower()))
		self._language_options = options
		self.language_choice.SetItems([label for __, label in options])
		target_index = 0
		for index, item in enumerate(options):
			if item[0] == current_key:
				target_index = index
				break
		self.language_choice.SetSelection(target_index)

	def _set_loading_state(self, is_loading, message=None):
		self.search_text.Enable(not is_loading)
		self.search_button.Enable(not is_loading)
		self.gender_choice.Enable(not is_loading)
		self.language_choice.Enable(not is_loading)
		self.hide_installed_checkbox.Enable(not is_loading)
		self.voice_list.Enable(not is_loading)
		self.preview_language_choice.Enable(not is_loading and not self._preview_in_progress)
		if is_loading:
			self.result_hint.SetLabel(message or _("Searching Hugging Face..."))
			self.empty_hint.SetLabel("")
			self.select_button.Enable(False)
			self.clear_button.Enable(False)
			self.preview_button.Enable(False)
			self.install_button.Enable(False)
		else:
			self._update_action_state()
			self._update_preview_lock_state()
		self.Layout()

	def _refresh_inventory_state(self):
		inventory = service.list_voice_inventory()
		self._installed_voice_ids = {record.voice_id for record in inventory["user"]}
		self._installed_voice_ids.update(record.voice_id for record in inventory["builtin"])

	def refresh_inventory_state(self):
		try:
			self._refresh_inventory_state()
		except Exception:
			log.exception("MaxLogic XTTS v2 Hugging Face inventory refresh failed")
			return
		self._apply_filters()

	def _finish_search(self, generation, entries=None, payload=None, error_message=None):
		if generation != self._search_generation:
			return
		if error_message:
			self.search_hint.SetLabel(_("Hugging Face results could not be loaded right now."))
			self.result_hint.SetLabel("")
			self.detail_hint.SetLabel("")
			self.empty_hint.SetLabel(_("Try a different query or try again in a moment."))
			self._entries = []
			self._visible_entries = []
			self.voice_list.SetItems([])
			self._set_loading_state(False)
			return
		self._entries = entries or []
		self._checked_ids.intersection_update({entry["id"] for entry in self._entries})
		self._last_payload = payload or {}
		try:
			self._refresh_inventory_state()
		except Exception:
			log.exception("MaxLogic XTTS v2 Hugging Face search inventory refresh failed")
			self._installed_voice_ids = set()
		self._update_language_choices()
		self.search_hint.SetLabel(_format_huggingface_hint(self._last_payload))
		self._set_loading_state(False)
		self._apply_filters()

	def search_entries(self, initial=False):
		query = self.search_text.GetValue().strip() or "xtts"
		self._last_query = query
		self._search_generation += 1
		generation = self._search_generation
		self._set_loading_state(True, _("Searching Hugging Face..."))
		if not initial:
			self.search_hint.SetLabel(_("Searching Hugging Face for '{query}'...").format(query=query))

		def _worker():
			try:
				entries, payload = service.search_huggingface_voices(query=query, limit=20)
			except Exception as error:
				wx.CallAfter(self._finish_search, generation, None, None, str(error))
				return
			wx.CallAfter(self._finish_search, generation, entries, payload, None)

		thread = threading.Thread(
			target=_worker,
			name="MaxLogicXTTSV2HuggingFaceSearch",
			daemon=True,
		)
		thread.start()

	def _apply_filters(self):
		gender_key = self._selected_gender_key()
		language_key = self._selected_language_key()
		hide_installed = self.hide_installed_checkbox.GetValue()
		visible_entries = []
		for entry in self._entries:
			if hide_installed and entry.get("installVoiceId") in self._installed_voice_ids:
				continue
			if gender_key != "all" and entry.get("gender", "unknown") != gender_key:
				continue
			if language_key and entry.get("language", "") != language_key:
				continue
			visible_entries.append(entry)
		self._visible_entries = visible_entries
		self.voice_list.SetItems([self._format_entry(entry) for entry in visible_entries])
		for index, entry in enumerate(visible_entries):
			self.voice_list.Check(index, entry["id"] in self._checked_ids)
		self.result_hint.SetLabel(
			_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
			if self._entries
			else _("No Hugging Face results loaded yet.")
		)
		if self._entries and not self._visible_entries:
			self.empty_hint.SetLabel(_("No voices match the current filters."))
		elif not self._entries:
			self.empty_hint.SetLabel(_("Press Search Hugging Face to load results."))
		else:
			self.empty_hint.SetLabel("")
		self._update_detail_hint()
		self._update_action_state()
		self.Layout()

	def _update_detail_hint(self):
		entry = self._focused_entry()
		if entry is None:
			self.detail_hint.SetLabel("")
			return
		self.detail_hint.SetLabel(
			_("Repo: {repo} | Sample: {sample} | License: {license} | Likes: {likes} | Downloads: {downloads}").format(
				repo=entry.get("hfModelId", entry["id"]),
				sample=entry.get("hfSamplePath") or entry.get("sourceFile") or _("unknown"),
				license=entry.get("license") or _("unknown"),
				likes=int(entry.get("likes") or 0),
				downloads=int(entry.get("downloads") or 0),
			)
		)

	def _update_action_state(self):
		has_visible = bool(self._visible_entries)
		self.select_button.Enable(has_visible and not self._preview_in_progress)
		self.clear_button.Enable(has_visible and not self._preview_in_progress)
		self.install_button.Enable(bool(self._checked_ids) and not self._preview_in_progress)
		self._update_preview_button_state()
		self._update_preview_lock_state()

	def on_toggle_entry(self, event):
		index = event.GetInt()
		if index == wx.NOT_FOUND or index >= len(self._visible_entries):
			return
		entry = self._visible_entries[index]
		if self.voice_list.IsChecked(index):
			self._checked_ids.add(entry["id"])
		else:
			self._checked_ids.discard(entry["id"])
		self.result_hint.SetLabel(
			_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
		)
		self._update_action_state()

	def on_select_visible(self):
		for entry in self._visible_entries:
			self._checked_ids.add(entry["id"])
		self._apply_filters()

	def on_clear_visible(self):
		for entry in self._visible_entries:
			self._checked_ids.discard(entry["id"])
		self._apply_filters()

	def _run_busy(self, message, callback):
		busy = wx.BusyInfo(message, parent=self)
		try:
			return callback()
		finally:
			del busy

	def _install_entry(self, entry, overwrite):
		return service.install_huggingface_voice(entry, overwrite=overwrite, refresh=False)

	def on_play_sample(self, event):
		if self._preview_playing:
			self._preview_request_id += 1
			service.stop_preview()
			self._preview_in_progress = False
			self._preview_playing = False
			self.preview_language_choice.Enable(True)
			self.result_hint.SetLabel(
				_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
			)
			self._update_action_state()
			return
		entry = self._focused_entry()
		if entry is None:
			gui.messageBox(
				_("Select one Hugging Face voice in the list to play a sample."),
				_("No voice selected"),
				wx.OK | wx.ICON_INFORMATION,
			)
			return
		self._preview_in_progress = True
		self._preview_playing = False
		self._preview_request_id += 1
		request_id = self._preview_request_id
		self.preview_language_choice.Enable(False)
		status_message = _("Generating sample for {name}. The first preview can take around a minute.").format(
			name=entry.get("displayName", entry["id"]),
		)
		self.result_hint.SetLabel(status_message)
		ui.message(status_message)
		self._update_action_state()

		def _on_started():
			if request_id != self._preview_request_id:
				return
			self._preview_playing = True
			self.result_hint.SetLabel(_("Playing sample for {name}.").format(name=entry.get("displayName", entry["id"])))
			self._update_action_state()

		def _on_complete(status, error_message):
			if request_id != self._preview_request_id:
				return
			self._preview_in_progress = False
			self._preview_playing = False
			self.preview_language_choice.Enable(True)
			if status in ("superseded", "stopped"):
				self.result_hint.SetLabel(
					_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
				)
				self._update_action_state()
				return
			if error_message:
				self.result_hint.SetLabel(_("Sample playback failed."))
				gui.messageBox(
					_("Sample playback failed.\nSee NVDA's log for details.\n{error}").format(error=error_message),
					_("Sample playback failed"),
					wx.OK | wx.ICON_ERROR,
				)
			else:
				self.result_hint.SetLabel(
					_format_count_hint(len(self._visible_entries), len(self._entries), len(self._checked_ids))
				)
			self._update_action_state()

		service.play_catalog_voice_sample(
			entry,
			on_complete=_on_complete,
			preview_language=self._selected_preview_language(),
			on_playback_started=_on_started,
		)

	def on_install_selected(self, event):
		selected_entries = [entry for entry in self._entries if entry["id"] in self._checked_ids]
		if not selected_entries:
			return
		installed = []
		overwritten = []
		duplicates = []
		failures = []

		def _initial_pass():
			for entry in selected_entries:
				try:
					result = self._install_entry(entry, overwrite=False)
				except service.DuplicateVoiceError:
					duplicates.append(entry)
				except Exception as error:
					log.exception("MaxLogic XTTS v2 Hugging Face install failed", exc_info=True)
					failures.append((entry, error))
				else:
					installed.extend(result["records"])

		self._run_busy(_("Installing selected Hugging Face voices..."), _initial_pass)

		if duplicates:
			response = gui.messageBox(
				_("{count} selected Hugging Face voices are already installed. Overwrite the user-managed copies?").format(
					count=len(duplicates)
				),
				_("Overwrite installed voices?"),
				wx.YES_NO | wx.ICON_WARNING,
			)
			if response == wx.YES:
				def _overwrite_pass():
					for entry in duplicates:
						try:
							result = self._install_entry(entry, overwrite=True)
						except Exception as error:
							log.exception("MaxLogic XTTS v2 Hugging Face overwrite failed", exc_info=True)
							failures.append((entry, error))
						else:
							overwritten.extend(result["records"])

				self._run_busy(_("Overwriting selected Hugging Face voices..."), _overwrite_pass)

		refresh = service.refresh_active_synth(
			reason="huggingface-batch-install",
			preferred_voice=(installed or overwritten)[0].voice_id if (installed or overwritten) else None,
		)
		self._checked_ids.clear()
		self._on_change()

		message_lines = []
		if installed:
			message_lines.append(_("Installed {count} voices.").format(count=len(installed)))
		if overwritten:
			message_lines.append(_("Overwrote {count} voices.").format(count=len(overwritten)))
		if duplicates and not overwritten:
			message_lines.append(_("Skipped {count} already-installed voices.").format(count=len(duplicates)))
		if failures:
			message_lines.append(_("Failed to install {count} voices. See NVDA's log for details.").format(count=len(failures)))
		if refresh.get("restartRequired"):
			message_lines.append(_("Restart NVDA to refresh the current synth."))
		if not message_lines:
			message_lines.append(_("No voice changes were applied."))
		title = _("Voice installation complete") if not failures else _("Voice installation completed with issues")
		gui.messageBox("\n".join(message_lines), title, wx.OK | wx.ICON_INFORMATION)


class BrowseVoicesPanel(wx.Panel):
	def __init__(self, parent, on_change):
		super(BrowseVoicesPanel, self).__init__(parent)
		self._source_options = [
			("huggingface", _("Hugging Face search")),
			("official", _("Official catalog")),
			("community", _("Community catalog")),
		]
		self._panels = {}
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		source_row = wx.BoxSizer(wx.HORIZONTAL)
		source_row.Add(wx.StaticText(self, label=_("Source")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.source_choice = wx.Choice(self, choices=[label for __, label in self._source_options])
		self.source_choice.SetSelection(0)
		source_row.Add(self.source_choice, 0, wx.ALL, 5)
		self.source_status = wx.StaticText(self, label="")
		source_row.Add(self.source_status, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		main_sizer.Add(source_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 0)
		self.content_sizer = wx.BoxSizer(wx.VERTICAL)
		main_sizer.Add(self.content_sizer, 1, wx.EXPAND)
		self.SetSizer(main_sizer)

		self._panels["huggingface"] = HuggingFaceSearchPanel(self, on_change=on_change)
		self._panels["official"] = CatalogVoicesPanel(
			self,
			on_change=on_change,
			catalog_name="official",
			title=_("Official XTTS profiles"),
			empty_message=_("No official XTTS profiles are available in the current catalog."),
			allow_refresh=True,
			show_hide_local_toggle=True,
		)
		self._panels["community"] = CatalogVoicesPanel(
			self,
			on_change=on_change,
			catalog_name="community",
			title=_("Community and experimental XTTS profiles"),
			empty_message=_("No curated community XTTS profiles are listed yet."),
			allow_refresh=False,
			show_hide_local_toggle=True,
		)
		for panel in self._panels.values():
			self.content_sizer.Add(panel, 1, wx.EXPAND)
		self.Bind(wx.EVT_CHOICE, self.on_source_changed, self.source_choice)
		self._show_source("huggingface")

	def _selected_source(self):
		index = self.source_choice.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self._source_options):
			return self._source_options[0][0]
		return self._source_options[index][0]

	def _source_label(self, source_key):
		for key, label in self._source_options:
			if key == source_key:
				return label
		return source_key

	def _show_source(self, source_key):
		for key, panel in self._panels.items():
			panel.Show(key == source_key)
		self.source_status.SetLabel(
			_("Browse source: {source}").format(source=self._source_label(source_key))
		)
		self.Layout()

	def on_source_changed(self, event):
		source_key = self._selected_source()
		self._show_source(source_key)
		log.info("MaxLogic XTTS v2 voice browser source selected. source=%s", source_key)
		event.Skip()

	def refresh_inventory_state(self):
		huggingface_panel = self._panels.get("huggingface")
		if huggingface_panel is not None:
			huggingface_panel.refresh_inventory_state()
		for key in ("official", "community"):
			panel = self._panels.get(key)
			if panel is not None:
				panel.refresh_entries(force_refresh=False)

	def active_source_payload(self):
		source_key = self._selected_source()
		panel = self._panels.get(source_key)
		return {
			"source": source_key,
			"label": self._source_label(source_key),
			"catalog": getattr(panel, "_catalog_name", None),
		}


class ExtractSamplePanel(wx.Panel):
	_AUDIO_WILDCARD = (
		_("Audio files (*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac)|*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac")
	)
	_FALLBACK_PLAY_WINDOW_MS = 30000

	def __init__(self, parent, on_change):
		super(ExtractSamplePanel, self).__init__(parent)
		self._on_change = on_change
		self._source_path = None
		self._source_display_path = None
		self._working_copy = None
		self._working_copy_modified = False
		self._source_info = None
		self._transport_ready = False
		self._playback_stop_at_ms = None
		self._active_preview_action = None
		self._save_busy = None
		self._busy = False
		self._media = None
		self._media_loaded = False
		self._fallback_playing = False
		self._fallback_generation = 0
		self._fallback_position_ms = 0
		self._fallback_start_ms = 0
		self._fallback_started_at = None
		self._timer = wx.Timer(self)
		if wxmedia is not None:
			try:
				self._media = wxmedia.MediaCtrl(self)
				self._media.Hide()
			except Exception:
				self._media = None
		self._build_ui()
		self.Bind(wx.EVT_TIMER, self.on_timer, self._timer)
		self._set_transport_ready(False)
		self._update_marker_summary()
		self.status_label.SetLabel(_("Choose a source recording to begin extracting a sample."))

	def _build_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		source_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("Source audio"))
		source_row = wx.BoxSizer(wx.HORIZONTAL)
		self.source_path_ctrl = wx.TextCtrl(self, style=wx.TE_READONLY)
		self.browse_button = wx.Button(self, label=_("Browse..."))
		source_row.Add(self.source_path_ctrl, 1, wx.ALL | wx.EXPAND, 5)
		source_row.Add(self.browse_button, 0, wx.ALL, 5)
		source_box.Add(source_row, 0, wx.EXPAND)
		self.source_summary = wx.StaticText(self, label=_("No source file selected."))
		source_box.Add(self.source_summary, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		main_sizer.Add(source_box, 0, wx.EXPAND | wx.ALL, 5)

		transport_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("Transport"))
		position_row = wx.BoxSizer(wx.HORIZONTAL)
		position_row.Add(wx.StaticText(self, label=_("Current position")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.current_position_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
		self.current_position_ctrl.SetValue(_format_timecode(0))
		position_row.Add(self.current_position_ctrl, 0, wx.ALL, 5)
		position_row.Add(wx.StaticText(self, label=_("Playback speed")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.speed_choice = wx.Choice(self, choices=["0.5x", "0.75x", "1.0x", "1.25x"])
		self.speed_choice.SetStringSelection("1.0x")
		position_row.Add(self.speed_choice, 0, wx.ALL, 5)
		transport_box.Add(position_row, 0, wx.EXPAND)

		button_row_1 = wx.BoxSizer(wx.HORIZONTAL)
		self.play_pause_button = wx.Button(self, label=_("Play"))
		self.stop_button = wx.Button(self, label=_("Stop"))
		self.back_5_button = wx.Button(self, label=_("Rewind 5s"))
		self.forward_5_button = wx.Button(self, label=_("Forward 5s"))
		for control in (self.play_pause_button, self.stop_button, self.back_5_button, self.forward_5_button):
			button_row_1.Add(control, 0, wx.ALL, 5)
		transport_box.Add(button_row_1, 0, wx.EXPAND)

		button_row_2 = wx.BoxSizer(wx.HORIZONTAL)
		self.back_30_button = wx.Button(self, label=_("Rewind 30s"))
		self.forward_30_button = wx.Button(self, label=_("Forward 30s"))
		for control in (self.back_30_button, self.forward_30_button):
			button_row_2.Add(control, 0, wx.ALL, 5)
		transport_box.Add(button_row_2, 0, wx.EXPAND)
		main_sizer.Add(transport_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

		marker_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("Selection"))
		marker_grid = wx.FlexGridSizer(cols=4, hgap=8, vgap=8)
		marker_grid.AddGrowableCol(1, 1)
		marker_grid.Add(wx.StaticText(self, label=_("Start marker")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.start_marker_ctrl = wx.TextCtrl(self)
		self.set_start_button = wx.Button(self, label=_("Set start at current position"))
		self.play_before_start_button = wx.Button(self, label=_("3s before start"))
		self.play_from_start_button = wx.Button(self, label=_("3s from start"))
		marker_grid.Add(self.start_marker_ctrl, 0, wx.EXPAND)
		marker_grid.Add(self.set_start_button, 0, wx.EXPAND)
		marker_grid.AddSpacer(1)
		marker_grid.AddSpacer(1)
		marker_grid.AddSpacer(1)
		marker_grid.Add(self.play_before_start_button, 0, wx.EXPAND)
		marker_grid.Add(self.play_from_start_button, 0, wx.EXPAND)
		marker_grid.Add(wx.StaticText(self, label=_("End marker")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.end_marker_ctrl = wx.TextCtrl(self)
		self.set_end_button = wx.Button(self, label=_("Set end at current position"))
		self.play_before_end_button = wx.Button(self, label=_("3s before end"))
		self.play_after_end_button = wx.Button(self, label=_("3s after end"))
		marker_grid.Add(self.end_marker_ctrl, 0, wx.EXPAND)
		marker_grid.Add(self.set_end_button, 0, wx.EXPAND)
		marker_grid.AddSpacer(1)
		marker_grid.AddSpacer(1)
		marker_grid.AddSpacer(1)
		marker_grid.Add(self.play_before_end_button, 0, wx.EXPAND)
		marker_grid.Add(self.play_after_end_button, 0, wx.EXPAND)
		marker_grid.Add(wx.StaticText(self, label=_("Selection length")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.selection_length_ctrl = wx.TextCtrl(self, style=wx.TE_READONLY)
		marker_grid.Add(self.selection_length_ctrl, 0, wx.EXPAND)
		marker_grid.AddSpacer(1)
		marker_grid.AddSpacer(1)
		marker_box.Add(marker_grid, 0, wx.EXPAND | wx.ALL, 5)
		self.selection_hint = wx.StaticText(self, label="")
		marker_box.Add(self.selection_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		main_sizer.Add(marker_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

		edit_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("Edit and export"))
		edit_actions = wx.FlexGridSizer(rows=0, cols=2, hgap=8, vgap=8)
		edit_actions.AddGrowableCol(0, 1)
		edit_actions.AddGrowableCol(1, 1)
		self.preview_selection_button = wx.Button(self, label=_("Preview selection"))
		self.delete_snippet_button = wx.Button(self, label=_("Delete snippet"))
		self.save_snippet_button = wx.Button(self, label=_("Save snippet as audio"))
		self.save_modified_audio_button = wx.Button(self, label=_("Save edited audio"))
		for control in (
			self.preview_selection_button,
			self.delete_snippet_button,
			self.save_snippet_button,
			self.save_modified_audio_button,
		):
			edit_actions.Add(control, 0, wx.EXPAND)
		edit_box.Add(edit_actions, 0, wx.EXPAND | wx.ALL, 5)
		self.edit_hint = wx.StaticText(
			self,
			label=_("Delete snippet edits only the temporary copy. Save edited audio is enabled after the first deletion."),
		)
		edit_box.Add(self.edit_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		main_sizer.Add(edit_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

		save_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("Save as XTTS profile"))
		voice_row = wx.BoxSizer(wx.HORIZONTAL)
		voice_row.Add(wx.StaticText(self, label=_("Voice name")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.voice_name_ctrl = wx.TextCtrl(self)
		voice_row.Add(self.voice_name_ctrl, 1, wx.ALL | wx.EXPAND, 5)
		save_box.Add(voice_row, 0, wx.EXPAND)
		self.normalize_checkbox = wx.CheckBox(self, label=_("Normalize sample volume"))
		self.normalize_checkbox.SetValue(True)
		self.trim_checkbox = wx.CheckBox(self, label=_("Trim silence at start and end"))
		self.trim_checkbox.SetValue(True)
		save_box.Add(self.normalize_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		save_box.Add(self.trim_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.recommendation_label = wx.StaticText(
			self,
			label=_("Best results: 10 to 30 seconds of clean speech with no music or effects."),
		)
		save_box.Add(self.recommendation_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.save_button = wx.Button(self, label=_("Save as XTTS profile"))
		save_box.Add(self.save_button, 0, wx.ALL, 5)
		main_sizer.Add(save_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
		self.SetSizer(main_sizer)

		self.Bind(wx.EVT_BUTTON, self.on_browse, self.browse_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_pause, self.play_pause_button)
		self.Bind(wx.EVT_BUTTON, self.on_stop, self.stop_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self._seek_relative(-5000), self.back_5_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self._seek_relative(5000), self.forward_5_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self._seek_relative(-30000), self.back_30_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self._seek_relative(30000), self.forward_30_button)
		self.Bind(wx.EVT_BUTTON, self.on_set_start, self.set_start_button)
		self.Bind(wx.EVT_BUTTON, self.on_set_end, self.set_end_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_before_start, self.play_before_start_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_from_start, self.play_from_start_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_before_end, self.play_before_end_button)
		self.Bind(wx.EVT_BUTTON, self.on_play_after_end, self.play_after_end_button)
		self.Bind(wx.EVT_BUTTON, self.on_preview_selection, self.preview_selection_button)
		self.Bind(wx.EVT_BUTTON, self.on_delete_snippet, self.delete_snippet_button)
		self.Bind(wx.EVT_BUTTON, self.on_save_current_snippet, self.save_snippet_button)
		self.Bind(wx.EVT_BUTTON, self.on_save_modified_audio, self.save_modified_audio_button)
		self.Bind(wx.EVT_BUTTON, self.on_save_profile, self.save_button)
		self.Bind(wx.EVT_TEXT_ENTER, self.on_current_position_enter, self.current_position_ctrl)
		self.Bind(wx.EVT_KILL_FOCUS, self.on_current_position_kill_focus, self.current_position_ctrl)
		self.Bind(wx.EVT_TEXT, lambda evt: self._update_marker_summary(), self.start_marker_ctrl)
		self.Bind(wx.EVT_TEXT, lambda evt: self._update_marker_summary(), self.end_marker_ctrl)
		self.Bind(wx.EVT_CHOICE, lambda evt: self._apply_speed(), self.speed_choice)
		self._bind_shortcuts()

	def _bind_shortcuts(self):
		def _command(handler):
			command_id = int(wx.NewIdRef())
			self.Bind(wx.EVT_MENU, handler, id=command_id)
			return command_id

		self.SetAcceleratorTable(
			wx.AcceleratorTable(
				[
					(wx.ACCEL_CTRL, ord("O"), _command(self.on_browse)),
					(wx.ACCEL_CTRL, ord("P"), _command(self.on_play_pause)),
					(wx.ACCEL_CTRL, ord("K"), _command(self.on_stop)),
					(wx.ACCEL_CTRL, wx.WXK_LEFT, _command(lambda evt: self._seek_relative(-5000))),
					(wx.ACCEL_CTRL, wx.WXK_RIGHT, _command(lambda evt: self._seek_relative(5000))),
					(wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_LEFT, _command(lambda evt: self._seek_relative(-30000))),
					(wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_RIGHT, _command(lambda evt: self._seek_relative(30000))),
					(wx.ACCEL_CTRL, ord("1"), _command(self.on_set_start)),
					(wx.ACCEL_CTRL, ord("2"), _command(self.on_set_end)),
					(wx.ACCEL_CTRL, ord("R"), _command(self.on_preview_selection)),
					(wx.ACCEL_CTRL, ord("D"), _command(self.on_delete_snippet)),
					(wx.ACCEL_CTRL, ord("E"), _command(self.on_save_current_snippet)),
					(wx.ACCEL_CTRL, ord("M"), _command(self.on_save_modified_audio)),
					(wx.ACCEL_CTRL, ord("S"), _command(self.on_save_profile)),
				]
			)
		)

	def _shortcut_hint(self):
		return _(
			"Shortcuts: Ctrl+O browse, Ctrl+P play/pause, Ctrl+K stop, Ctrl+Left/Right 5s, Ctrl+Shift+Left/Right 30s, Ctrl+1 start, Ctrl+2 end, Ctrl+R preview, Ctrl+D delete, Ctrl+E save snippet, Ctrl+M save edited audio, Ctrl+S save profile."
		)

	def _control_available(self, control):
		return control is not None and control.IsEnabled() and control.IsShownOnScreen() and not self._busy

	def _set_working_copy_modified(self, modified):
		self._working_copy_modified = bool(modified)
		self._update_transport_controls()
		self.Layout()

	def _source_stem(self):
		path = self._source_display_path or self._source_path or "xtts-sample"
		stem = os.path.splitext(os.path.basename(path))[0].strip()
		return stem or "xtts-sample"

	def _source_extension(self):
		if self._working_copy is not None:
			working_path = self._working_copy.get("workingPath") or ""
			extension = os.path.splitext(working_path)[1].lower()
			if extension:
				return extension
		return ".wav"

	def _filename_timecode(self, ms):
		return _format_timecode(ms).replace(":", "-").replace(".", "-")

	def _save_audio_dialog(self, title, default_name, wildcard):
		dialog = wx.FileDialog(
			parent=gui.mainFrame,
			message=title,
			defaultFile=default_name,
			wildcard=wildcard,
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		)
		gui.mainFrame.prePopup()
		try:
			result_code = dialog.ShowModal()
		finally:
			gui.mainFrame.postPopup()
		if result_code != wx.ID_OK:
			return None
		return dialog.GetPath().strip() or None

	def _finish_audio_export(self, message, result=None, error_message=None):
		self._set_busy(False)
		if error_message:
			gui.messageBox(
				_("{message}\nSee NVDA's log for details.\n{error}").format(message=message, error=error_message),
				_("Audio save failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		path = result.get("path") if isinstance(result, dict) else None
		self.status_label.SetLabel(
			_("Saved audio file: {path}").format(path=path or _("unknown path"))
		)

	def _set_transport_ready(self, ready):
		self._transport_ready = bool(ready)
		self._update_transport_controls()

	def _update_transport_controls(self):
		enabled = self._transport_ready and self._save_busy is None and not self._busy
		for control in (
			self.play_pause_button,
			self.stop_button,
			self.back_5_button,
			self.forward_5_button,
			self.back_30_button,
			self.forward_30_button,
			self.set_start_button,
			self.set_end_button,
			self.play_before_start_button,
			self.play_from_start_button,
			self.play_before_end_button,
			self.play_after_end_button,
			self.preview_selection_button,
			self.delete_snippet_button,
			self.save_snippet_button,
		):
			control.Enable(enabled)
		self.current_position_ctrl.Enable(enabled)
		self.save_modified_audio_button.Enable(enabled and self._working_copy_modified)
		self.speed_choice.Enable(enabled and self._media_loaded)
		self.save_button.Enable((self._source_info is not None) and self._save_busy is None and not self._busy)

	def _set_busy(self, is_busy, message=None):
		self._busy = bool(is_busy)
		for control in (
			self.browse_button,
			self.source_path_ctrl,
			self.current_position_ctrl,
			self.start_marker_ctrl,
			self.end_marker_ctrl,
			self.voice_name_ctrl,
			self.normalize_checkbox,
			self.trim_checkbox,
		):
			control.Enable(not is_busy)
		self._update_transport_controls()
		if message:
			self.status_label.SetLabel(message)

	def _apply_speed(self):
		if self._media is None or not self._media_loaded:
			return
		try:
			value = self.speed_choice.GetStringSelection().rstrip("x")
			self._media.SetPlaybackRate(float(value or "1.0"))
		except Exception:
			pass

	def _selected_speed(self):
		if not self._media_loaded:
			return 1.0
		try:
			value = self.speed_choice.GetStringSelection().rstrip("x")
			return max(0.1, float(value or "1.0"))
		except Exception:
			return 1.0

	def _current_duration_ms(self):
		if self._source_info is None:
			return 0
		return int(self._source_info.get("durationMs") or 0)

	def _current_position_ms(self):
		if not self._media_loaded and self._fallback_playing:
			if self._fallback_started_at is None:
				return max(0, min(int(self._fallback_position_ms), self._current_duration_ms()))
			elapsed_ms = int(round((time.monotonic() - self._fallback_started_at) * 1000.0 * self._selected_speed()))
			position = self._fallback_start_ms + elapsed_ms
			stop_at_ms = self._playback_stop_at_ms
			if stop_at_ms is not None:
				position = min(position, int(stop_at_ms))
			return max(0, min(position, self._current_duration_ms()))
		if not self._media_loaded:
			return max(0, min(int(self._fallback_position_ms), self._current_duration_ms()))
		if self._media is None or not self._media_loaded:
			return 0
		try:
			position = int(self._media.Tell())
		except Exception:
			return 0
		return max(0, position)

	def _seek_absolute(self, position_ms):
		target = max(0, min(int(position_ms), self._current_duration_ms()))
		if self._media is None or not self._media_loaded:
			was_playing = self._fallback_playing
			stop_at_ms = self._playback_stop_at_ms
			self._stop_playback(reset_stop_at=False)
			self._fallback_position_ms = target
			self.current_position_ctrl.SetValue(_format_timecode(target))
			if was_playing:
				self._start_playback(start_ms=target, stop_at_ms=stop_at_ms)
			return
		try:
			self._media.Seek(target)
		except Exception:
			return
		self.current_position_ctrl.SetValue(_format_timecode(target))

	def _commit_current_position_from_control(self, show_errors=False):
		if not self._transport_ready:
			self.current_position_ctrl.SetValue(_format_timecode(0))
			return False
		try:
			target = _parse_timecode(self.current_position_ctrl.GetValue())
		except Exception as error:
			self.current_position_ctrl.SetValue(_format_timecode(self._current_position_ms()))
			if show_errors:
				gui.messageBox(str(error), _("Invalid current position"), wx.OK | wx.ICON_WARNING)
			return False
		target = max(0, min(int(target), self._current_duration_ms()))
		self._seek_absolute(target)
		return True

	def _seek_relative(self, delta_ms):
		if not self._transport_ready or self._busy:
			return
		target = self._current_position_ms() + int(delta_ms)
		self._seek_absolute(target)
		ui.message(_("Position {time}").format(time=_format_timecode(self._current_position_ms())))

	def _playing(self):
		if not self._media_loaded:
			return self._fallback_playing
		if self._media is None or not self._media_loaded or wxmedia is None:
			return False
		try:
			return self._media.GetState() == wxmedia.MEDIASTATE_PLAYING
		except Exception:
			return False

	def _paused(self):
		if self._media is None or not self._media_loaded or wxmedia is None:
			return False
		try:
			return self._media.GetState() == wxmedia.MEDIASTATE_PAUSED
		except Exception:
			return False

	def _toggle_preview_stop(self, preview_action):
		if self._playing() and self._active_preview_action == preview_action:
			self._stop_playback(reset_stop_at=True)
			return True
		return False

	def _start_timer(self):
		if not self._timer.IsRunning():
			self._timer.Start(200)

	def _stop_timer(self):
		if self._timer.IsRunning():
			self._timer.Stop()

	def _stop_playback(self, reset_stop_at=True):
		if self._media is not None and self._media_loaded:
			try:
				self._media.Stop()
			except Exception:
				pass
		elif self._fallback_playing:
			self._fallback_position_ms = self._current_position_ms()
			self._fallback_generation += 1
			service.stop_preview()
			self._fallback_playing = False
			self._fallback_started_at = None
		if reset_stop_at:
			self._playback_stop_at_ms = None
		self._active_preview_action = None
		self.play_pause_button.SetLabel(_("Play"))
		self._stop_timer()

	def _reset_media_control(self):
		self._stop_playback(reset_stop_at=True)
		if self._media is not None:
			try:
				self._media.Destroy()
			except Exception:
				pass
		self._media = None
		self._media_loaded = False
		if wxmedia is not None:
			try:
				self._media = wxmedia.MediaCtrl(self)
				self._media.Hide()
			except Exception:
				self._media = None

	def _start_playback(self, start_ms=None, stop_at_ms=None, preview_action=None):
		if not self._transport_ready:
			return
		if start_ms is not None:
			self._seek_absolute(start_ms)
		self._playback_stop_at_ms = stop_at_ms
		self._active_preview_action = preview_action
		self._apply_speed()
		if self._media is None or not self._media_loaded:
			self._start_fallback_playback(start_ms=start_ms, stop_at_ms=stop_at_ms, preview_action=preview_action)
			return
		try:
			self._media.Play()
		except Exception as error:
			gui.messageBox(
				_("Playback failed.\n{error}").format(error=error),
				_("Playback failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self.play_pause_button.SetLabel(_("Pause"))
		self._start_timer()

	def _start_fallback_playback(self, start_ms=None, stop_at_ms=None, preview_action=None):
		if self._source_path is None:
			return
		start_ms = self._current_position_ms() if start_ms is None else int(start_ms)
		duration_ms = self._current_duration_ms()
		end_ms = duration_ms if stop_at_ms is None else int(stop_at_ms)
		start_ms = max(0, min(start_ms, self._current_duration_ms()))
		end_ms = max(0, min(end_ms, self._current_duration_ms()))
		streamable_wav = os.path.splitext(self._source_path)[1].lower() == ".wav"
		max_end_ms = duration_ms if streamable_wav else min(duration_ms, start_ms + self._FALLBACK_PLAY_WINDOW_MS)
		if end_ms > max_end_ms:
			end_ms = max_end_ms
			self.status_label.SetLabel(
				_("Fallback playback is limited to {duration} from the current position.").format(
					duration=_format_timecode(self._FALLBACK_PLAY_WINDOW_MS),
				)
			)
		if end_ms <= start_ms:
			self._fallback_position_ms = start_ms
			self.current_position_ctrl.SetValue(_format_timecode(start_ms))
			return
		self._fallback_generation += 1
		generation = self._fallback_generation
		self._fallback_start_ms = start_ms
		self._fallback_position_ms = start_ms
		self._fallback_started_at = None
		self._fallback_playing = True
		self._playback_stop_at_ms = stop_at_ms
		self._active_preview_action = preview_action
		self.play_pause_button.SetLabel(_("Pause"))
		self._start_timer()

		def _on_started():
			if generation != self._fallback_generation:
				return
			self._fallback_started_at = time.monotonic()

		def _on_complete(status, error_message):
			if generation != self._fallback_generation:
				return
			self._fallback_playing = False
			self._fallback_started_at = None
			if status in ("completed", "superseded", "stopped"):
				self._fallback_position_ms = end_ms if status == "completed" else self._current_position_ms()
				self.current_position_ctrl.SetValue(_format_timecode(self._fallback_position_ms))
				self._active_preview_action = None
				self.play_pause_button.SetLabel(_("Play"))
				self._stop_timer()
				return
			self.play_pause_button.SetLabel(_("Play"))
			self._active_preview_action = None
			self._stop_timer()
			gui.messageBox(
				_("Playback failed.\n{error}").format(error=error_message or _("Unknown error")),
				_("Playback failed"),
				wx.OK | wx.ICON_ERROR,
			)

		service.play_audio_source_segment(
			self._source_path,
			start_ms=start_ms,
			end_ms=end_ms,
			on_complete=_on_complete,
			on_playback_started=_on_started,
		)

	def _validate_selection(self):
		if self._source_info is None:
			raise ValueError(_("Choose a source file first."))
		start_ms = _parse_timecode(self.start_marker_ctrl.GetValue())
		end_ms = _parse_timecode(self.end_marker_ctrl.GetValue())
		duration_ms = self._current_duration_ms()
		if duration_ms and end_ms > duration_ms:
			raise ValueError(_("End marker cannot be beyond the file duration."))
		if end_ms <= start_ms:
			raise ValueError(_("End marker must be after start marker."))
		return start_ms, end_ms, end_ms - start_ms

	def _update_marker_summary(self):
		try:
			__start_ms, __end_ms, length_ms = self._validate_selection()
		except Exception:
			self.selection_length_ctrl.SetValue(_("Not ready"))
			self.selection_hint.SetLabel(self._shortcut_hint())
			return
		self.selection_length_ctrl.SetValue(_format_timecode(length_ms))
		if 10000 <= length_ms <= 30000:
			quality = _("Selection length is in the recommended XTTS range.")
		elif length_ms < 10000:
			quality = _("Selection is short; aim for 10 to 30 seconds of clean speech.")
		else:
			quality = _("Selection is long; XTTS usually works better with 10 to 30 seconds.")
		self.selection_hint.SetLabel(_("{quality} {shortcuts}").format(quality=quality, shortcuts=self._shortcut_hint()))

	def _load_media_source(self, path):
		self._stop_playback()
		self._media_loaded = False
		if self._media is None:
			return False
		try:
			loaded = bool(self._media.Load(path))
		except Exception:
			return False
		if loaded:
			self._media_loaded = True
			self._apply_speed()
		return loaded

	def _cleanup_working_source(self):
		self._stop_playback(reset_stop_at=True)
		if self._working_copy is not None:
			try:
				service.delete_audio_working_copy(self._working_copy)
			except Exception:
				log.warning("MaxLogic XTTS v2 temporary source cleanup failed", exc_info=True)
		self._working_copy = None
		self._set_working_copy_modified(False)

	def _finish_source_load(self, display_path, working_copy=None, source_info=None, error_message=None):
		self._working_copy = working_copy if error_message is None else None
		self._source_display_path = display_path if error_message is None else None
		self._source_path = working_copy.get("workingPath") if working_copy is not None and error_message is None else None
		self._set_working_copy_modified(False)
		self._source_info = source_info
		if error_message:
			self.source_summary.SetLabel(_("Unable to read audio metadata right now."))
			self.status_label.SetLabel(_("Source load failed."))
			self._set_transport_ready(False)
			gui.messageBox(
				_("Could not open the source audio.\nSee NVDA's log for details.\n{error}").format(error=error_message),
				_("Source load failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self.source_path_ctrl.SetValue(display_path)
		loaded_in_transport = self._load_media_source(self._source_path)
		self.source_summary.SetLabel(
			_("{duration} | {sampleRate} Hz | {channels} channel(s) | {format}/{subtype} | editing temporary copy").format(
				duration=_format_timecode(source_info.get("durationMs") or 0),
				sampleRate=source_info.get("sampleRate") or 0,
				channels=source_info.get("channels") or 0,
				format=source_info.get("format") or _("Unknown"),
				subtype=source_info.get("subtype") or _("Unknown"),
			)
		)
		self.current_position_ctrl.SetValue(_format_timecode(0))
		self._fallback_position_ms = 0
		self.start_marker_ctrl.SetValue(_format_timecode(0))
		default_end = min(int(source_info.get("durationMs") or 0), 30000)
		if default_end <= 0:
			default_end = int(source_info.get("durationMs") or 0)
		self.end_marker_ctrl.SetValue(_format_timecode(default_end))
		if not self.voice_name_ctrl.GetValue().strip():
			base_name = os.path.splitext(os.path.basename(display_path))[0].replace("_", " ").strip()
			self.voice_name_ctrl.SetValue(base_name)
		self._set_transport_ready(True)
		if loaded_in_transport:
			self.status_label.SetLabel(_("Source ready. Set markers and preview the selection."))
		else:
			self.status_label.SetLabel(_("Source ready. Using fallback playback because the system media control could not load this file."))
		self._update_marker_summary()

	def on_browse(self, event):
		if not self._control_available(self.browse_button):
			return
		dialog = wx.FileDialog(
			parent=gui.mainFrame,
			message=_("Choose a source recording"),
			wildcard=self._AUDIO_WILDCARD,
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		gui.mainFrame.prePopup()
		try:
			result_code = dialog.ShowModal()
		finally:
			gui.mainFrame.postPopup()
		if result_code != wx.ID_OK:
			return
		path = dialog.GetPath().strip()
		if not path:
			return
		self._cleanup_working_source()
		self._source_path = None
		self._source_display_path = None
		self._source_info = None
		self._set_transport_ready(False)
		self._set_busy(True, _("Loading source audio..."))

		def _worker():
			working_copy = None
			try:
				working_copy = service.create_audio_working_copy(path)
				source_info = service.probe_audio_source(working_copy["workingPath"])
			except Exception as error:
				if working_copy is not None:
					service.delete_audio_working_copy(working_copy)
				log.exception("MaxLogic XTTS v2 source probe failed", exc_info=True)
				wx.CallAfter(self._set_busy, False)
				wx.CallAfter(self._finish_source_load, path, None, None, str(error))
				return
			wx.CallAfter(self._set_busy, False)
			wx.CallAfter(self._finish_source_load, path, working_copy, source_info, None)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2SourceProbe", daemon=True)
		thread.start()

	def on_play_pause(self, event):
		if not self._control_available(self.play_pause_button) or not self._transport_ready:
			return
		if self._playing():
			if self._media is not None and self._media_loaded:
				try:
					self._media.Pause()
				except Exception:
					return
				self.play_pause_button.SetLabel(_("Play"))
				self._stop_timer()
			else:
				self._stop_playback(reset_stop_at=False)
			return
		if wx.Window.FindFocus() is self.current_position_ctrl:
			if not self._commit_current_position_from_control(show_errors=True):
				return
		self._start_playback(start_ms=None, stop_at_ms=None)

	def on_stop(self, event):
		if not self._control_available(self.stop_button):
			return
		self._stop_playback(reset_stop_at=True)
		self._seek_absolute(0)

	def on_timer(self, event):
		position_ms = self._current_position_ms()
		if wx.Window.FindFocus() is not self.current_position_ctrl:
			self.current_position_ctrl.SetValue(_format_timecode(position_ms))
		stop_at_ms = self._playback_stop_at_ms
		if stop_at_ms is not None and position_ms >= stop_at_ms:
			self._stop_playback(reset_stop_at=True)
			self._seek_absolute(stop_at_ms)
			return
		if not self._playing():
			self.play_pause_button.SetLabel(_("Play"))
			self._active_preview_action = None
			self._stop_timer()

	def on_current_position_enter(self, event):
		self._commit_current_position_from_control(show_errors=True)

	def on_current_position_kill_focus(self, event):
		self._commit_current_position_from_control(show_errors=False)
		event.Skip()

	def on_set_start(self, event):
		if not self._control_available(self.set_start_button):
			return
		position_ms = self._current_position_ms()
		self.start_marker_ctrl.SetValue(_format_timecode(position_ms))
		self._update_marker_summary()
		ui.message(_("Start marker set to {time}").format(time=_format_timecode(position_ms)))

	def on_set_end(self, event):
		if not self._control_available(self.set_end_button):
			return
		position_ms = self._current_position_ms()
		self.end_marker_ctrl.SetValue(_format_timecode(position_ms))
		self._update_marker_summary()
		ui.message(_("End marker set to {time}").format(time=_format_timecode(position_ms)))

	def on_play_before_start(self, event):
		if not self._control_available(self.play_before_start_button):
			return
		if self._toggle_preview_stop("before-start"):
			return
		try:
			start_ms = _parse_timecode(self.start_marker_ctrl.GetValue())
		except Exception as error:
			gui.messageBox(str(error), _("Invalid start marker"), wx.OK | wx.ICON_WARNING)
			return
		self._start_playback(start_ms=max(0, start_ms - 3000), stop_at_ms=start_ms, preview_action="before-start")

	def on_play_from_start(self, event):
		if not self._control_available(self.play_from_start_button):
			return
		if self._toggle_preview_stop("from-start"):
			return
		try:
			start_ms = _parse_timecode(self.start_marker_ctrl.GetValue())
		except Exception as error:
			gui.messageBox(str(error), _("Invalid start marker"), wx.OK | wx.ICON_WARNING)
			return
		self._start_playback(
			start_ms=start_ms,
			stop_at_ms=min(self._current_duration_ms(), start_ms + 3000),
			preview_action="from-start",
		)

	def on_play_before_end(self, event):
		if not self._control_available(self.play_before_end_button):
			return
		if self._toggle_preview_stop("before-end"):
			return
		try:
			end_ms = _parse_timecode(self.end_marker_ctrl.GetValue())
		except Exception as error:
			gui.messageBox(str(error), _("Invalid end marker"), wx.OK | wx.ICON_WARNING)
			return
		self._start_playback(start_ms=max(0, end_ms - 3000), stop_at_ms=end_ms, preview_action="before-end")

	def on_play_after_end(self, event):
		if not self._control_available(self.play_after_end_button):
			return
		if self._toggle_preview_stop("after-end"):
			return
		try:
			end_ms = _parse_timecode(self.end_marker_ctrl.GetValue())
		except Exception as error:
			gui.messageBox(str(error), _("Invalid end marker"), wx.OK | wx.ICON_WARNING)
			return
		self._start_playback(
			start_ms=end_ms,
			stop_at_ms=min(self._current_duration_ms(), end_ms + 3000),
			preview_action="after-end",
		)

	def on_preview_selection(self, event):
		if not self._control_available(self.preview_selection_button):
			return
		if self._toggle_preview_stop("selection"):
			return
		try:
			start_ms, end_ms, __length_ms = self._validate_selection()
		except Exception as error:
			gui.messageBox(str(error), _("Selection not ready"), wx.OK | wx.ICON_WARNING)
			return
		self._start_playback(start_ms=start_ms, stop_at_ms=end_ms, preview_action="selection")

	def _finish_delete_snippet(self, start_ms, result=None, error_message=None):
		self._set_busy(False)
		if error_message:
			gui.messageBox(
				_("Deleting the selected snippet failed.\nSee NVDA's log for details.\n{error}").format(error=error_message),
				_("Delete snippet failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self._source_info = result
		self._set_working_copy_modified(True)
		duration_ms = self._current_duration_ms()
		position_ms = max(0, min(int(start_ms), duration_ms))
		self.current_position_ctrl.SetValue(_format_timecode(position_ms))
		self._fallback_position_ms = position_ms
		self.start_marker_ctrl.SetValue(_format_timecode(position_ms))
		end_ms = min(duration_ms, position_ms + 30000)
		if end_ms <= position_ms:
			end_ms = duration_ms
		self.end_marker_ctrl.SetValue(_format_timecode(end_ms))
		self._load_media_source(self._source_path)
		self.source_summary.SetLabel(
			_("{duration} | {sampleRate} Hz | {channels} channel(s) | {format}/{subtype} | editing temporary copy").format(
				duration=_format_timecode(result.get("durationMs") or 0),
				sampleRate=result.get("sampleRate") or 0,
				channels=result.get("channels") or 0,
				format=result.get("format") or _("Unknown"),
				subtype=result.get("subtype") or _("Unknown"),
			)
		)
		self._set_transport_ready(True)
		self._update_marker_summary()
		self.status_label.SetLabel(
			_("Deleted {duration} from the temporary audio copy.").format(
				duration=_format_timecode(result.get("deletedDurationMs") or 0),
			)
		)
		self.Layout()

	def on_save_current_snippet(self, event):
		if not self._control_available(self.save_snippet_button):
			return
		if self._source_path is None:
			return
		try:
			start_ms, end_ms, __length_ms = self._validate_selection()
		except Exception as error:
			gui.messageBox(str(error), _("Selection not ready"), wx.OK | wx.ICON_WARNING)
			return
		default_name = "%s-snippet-%s-%s.wav" % (
			self._source_stem(),
			self._filename_timecode(start_ms),
			self._filename_timecode(end_ms),
		)
		target_path = self._save_audio_dialog(
			_("Save current snippet as audio"),
			default_name,
			_("WAV files (*.wav)|*.wav"),
		)
		if not target_path:
			return
		if not os.path.splitext(target_path)[1]:
			target_path += ".wav"
		self._set_busy(True, _("Saving selected snippet as audio..."))

		def _worker():
			try:
				result = service.export_audio_source_segment(self._source_path, target_path, start_ms, end_ms)
			except Exception as error:
				log.exception("MaxLogic XTTS v2 source snippet export failed", exc_info=True)
				wx.CallAfter(self._finish_audio_export, _("Saving the selected snippet failed."), None, str(error))
				return
			wx.CallAfter(self._finish_audio_export, _("Saving the selected snippet failed."), result, None)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2SourceExport", daemon=True)
		thread.start()

	def on_save_modified_audio(self, event):
		if not self._control_available(self.save_modified_audio_button):
			return
		if self._working_copy is None:
			return
		extension = self._source_extension()
		default_name = "%s-edited%s" % (self._source_stem(), extension)
		target_path = self._save_audio_dialog(
			_("Save edited audio as"),
			default_name,
			_("{ext} files (*{ext})|*{ext}|All files (*.*)|*.*").format(ext=extension),
		)
		if not target_path:
			return
		if not os.path.splitext(target_path)[1]:
			target_path += extension
		self._set_busy(True, _("Saving edited temporary audio..."))

		def _worker():
			try:
				result = service.save_audio_working_copy(self._working_copy, target_path)
			except Exception as error:
				log.exception("MaxLogic XTTS v2 edited source export failed", exc_info=True)
				wx.CallAfter(self._finish_audio_export, _("Saving the edited audio failed."), None, str(error))
				return
			wx.CallAfter(self._finish_audio_export, _("Saving the edited audio failed."), result, None)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2EditedSourceExport", daemon=True)
		thread.start()

	def on_delete_snippet(self, event):
		if not self._control_available(self.delete_snippet_button):
			return
		if self._source_path is None:
			return
		try:
			start_ms, end_ms, length_ms = self._validate_selection()
		except Exception as error:
			gui.messageBox(str(error), _("Selection not ready"), wx.OK | wx.ICON_WARNING)
			return
		response = gui.messageBox(
			_("Delete this {duration} snippet from the temporary working copy? The original file will not be changed.").format(
				duration=_format_timecode(length_ms),
			),
			_("Delete snippet?"),
			wx.YES_NO | wx.ICON_WARNING,
		)
		if response != wx.YES:
			return
		self._reset_media_control()
		self._set_busy(True, _("Deleting selected snippet from temporary audio..."))

		def _worker():
			try:
				result = service.delete_audio_source_segment(self._source_path, start_ms, end_ms)
			except Exception as error:
				log.exception("MaxLogic XTTS v2 source snippet delete failed", exc_info=True)
				wx.CallAfter(self._finish_delete_snippet, start_ms, None, str(error))
				return
			wx.CallAfter(self._finish_delete_snippet, start_ms, result, None)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2SourceDelete", daemon=True)
		thread.start()

	def _selection_warning(self, length_ms):
		if 10000 <= length_ms <= 30000:
			return True
		response = gui.messageBox(
			_(
				"This selection is {duration}. XTTS usually works best with about 10 to 30 seconds of clean speech.\nDo you want to continue?"
			).format(duration=_format_timecode(length_ms)),
			_("Selection length warning"),
			wx.YES_NO | wx.ICON_WARNING,
		)
		return response == wx.YES

	def _finish_save(self, result=None, error_message=None, duplicate=False):
		if self._save_busy is not None:
			del self._save_busy
			self._save_busy = None
		self._set_busy(False)
		if duplicate:
			response = gui.messageBox(
				_("A voice with this name already exists. Do you want to overwrite it?"),
				_("Voice already exists"),
				wx.YES_NO | wx.ICON_WARNING,
			)
			if response == wx.YES:
				self._begin_save(overwrite=True)
			return
		if error_message:
			gui.messageBox(
				_("Saving the XTTS profile failed.\nSee NVDA's log for details.\n{error}").format(error=error_message),
				_("Save failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self.status_label.SetLabel(_("XTTS profile saved successfully."))
		self._on_change()
		message = _("Saved XTTS profile successfully.")
		if result["refresh"].get("restartRequired"):
			message += "\n" + _("Restart NVDA to refresh the current synth.")
		gui.messageBox(message, _("Profile saved"), wx.OK | wx.ICON_INFORMATION)

	def _begin_save(self, overwrite=False):
		try:
			start_ms, end_ms, length_ms = self._validate_selection()
		except Exception as error:
			gui.messageBox(str(error), _("Selection not ready"), wx.OK | wx.ICON_WARNING)
			return
		voice_name = self.voice_name_ctrl.GetValue().strip()
		if not voice_name:
			gui.messageBox(
				_("Enter a voice name for the extracted XTTS profile."),
				_("Voice name required"),
				wx.OK | wx.ICON_INFORMATION,
			)
			return
		if not self._selection_warning(length_ms):
			return
		self._save_busy = wx.BusyInfo(_("Saving XTTS profile..."), parent=self)
		self._set_busy(
			True,
			_("Extracting audio and saving the XTTS profile..."),
		)

		def _worker():
			try:
				result = service.extract_sample_to_voice(
					self._source_path,
					voice_name=voice_name,
					start_ms=start_ms,
					end_ms=end_ms,
					normalize=self.normalize_checkbox.GetValue(),
					trim_silence=self.trim_checkbox.GetValue(),
					overwrite=overwrite,
				)
			except service.DuplicateVoiceError:
				wx.CallAfter(self._finish_save, None, None, True)
				return
			except Exception as error:
				log.exception("MaxLogic XTTS v2 sample extraction failed", exc_info=True)
				wx.CallAfter(self._finish_save, None, str(error), False)
				return
			wx.CallAfter(self._finish_save, result, None, False)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2SampleSave", daemon=True)
		thread.start()

	def on_save_profile(self, event):
		if not self._control_available(self.save_button):
			return
		if self._source_info is None:
			gui.messageBox(
				_("Choose a source recording first."),
				_("No source selected"),
				wx.OK | wx.ICON_INFORMATION,
			)
			return
		self._begin_save(overwrite=False)

	def cleanup(self):
		self._stop_playback(reset_stop_at=True)
		if self._media is not None:
			try:
				self._media.Destroy()
			except Exception:
				pass
			self._media = None
		self._cleanup_working_source()


class SpeechCachePanel(wx.Panel):
	def __init__(self, parent):
		super(SpeechCachePanel, self).__init__(parent)
		self._mode_options = list(service.CACHE_MODE_OPTIONS)
		self._custom_limits = {"min": 2, "max": 80}
		self._refresh_generation = 0
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		main_sizer.Add(wx.StaticText(self, label=_("Speech cache settings")), 0, wx.ALL, 5)

		self.enable_checkbox = wx.CheckBox(self, label=_("Enable speech cache"))
		main_sizer.Add(self.enable_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

		form = wx.FlexGridSizer(cols=2, hgap=12, vgap=10)
		form.AddGrowableCol(1, 1)
		form.Add(wx.StaticText(self, label=_("Cache mode")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.mode_choice = wx.Choice(self, choices=[label for __, label in self._mode_options])
		form.Add(self.mode_choice, 0, wx.EXPAND)
		form.Add(wx.StaticText(self, label=_("Maximum cache size (MB)")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.max_size_ctrl = wx.SpinCtrl(self, min=16, max=4096, initial=256)
		form.Add(self.max_size_ctrl, 0, wx.EXPAND)
		form.Add(wx.StaticText(self, label=_("Minimum utterance length (characters)")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.min_chars_ctrl = wx.SpinCtrl(self, min=1, max=256, initial=2)
		form.Add(self.min_chars_ctrl, 0, wx.EXPAND)
		form.Add(wx.StaticText(self, label=_("Maximum utterance length (characters)")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.max_chars_ctrl = wx.SpinCtrl(self, min=1, max=512, initial=80)
		form.Add(self.max_chars_ctrl, 0, wx.EXPAND)
		main_sizer.Add(form, 0, wx.EXPAND | wx.ALL, 5)

		self.mode_hint = wx.StaticText(self, label="")
		main_sizer.Add(self.mode_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

		stats_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("Current cache"))
		self.path_label = wx.StaticText(self, label="")
		self.persistent_size_label = wx.StaticText(self, label="")
		self.persistent_entries_label = wx.StaticText(self, label="")
		self.hot_size_label = wx.StaticText(self, label="")
		self.hot_entries_label = wx.StaticText(self, label="")
		stats_box.Add(self.path_label, 0, wx.ALL, 5)
		stats_box.Add(self.persistent_size_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		stats_box.Add(self.persistent_entries_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		stats_box.Add(self.hot_size_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		stats_box.Add(self.hot_entries_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		main_sizer.Add(stats_box, 0, wx.EXPAND | wx.ALL, 5)

		button_row = wx.BoxSizer(wx.HORIZONTAL)
		self.save_button = wx.Button(self, label=_("Save cache settings"))
		self.refresh_button = wx.Button(self, label=_("Refresh cache stats"))
		self.compact_button = wx.Button(self, label=_("Compact cache"))
		self.clear_button = wx.Button(self, label=_("Clear cache"))
		button_row.Add(self.save_button, 0, wx.ALL, 5)
		button_row.Add(self.refresh_button, 0, wx.ALL, 5)
		button_row.Add(self.compact_button, 0, wx.ALL, 5)
		button_row.Add(self.clear_button, 0, wx.ALL, 5)
		main_sizer.Add(button_row, 0, wx.ALL, 0)

		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.SetSizer(main_sizer)

		self.Bind(wx.EVT_CHECKBOX, lambda evt: self._update_custom_state(), self.enable_checkbox)
		self.Bind(wx.EVT_CHOICE, lambda evt: self._update_custom_state(), self.mode_choice)
		self.Bind(wx.EVT_SPINCTRL, self._on_custom_limit_change, self.min_chars_ctrl)
		self.Bind(wx.EVT_SPINCTRL, self._on_custom_limit_change, self.max_chars_ctrl)
		self.Bind(wx.EVT_BUTTON, self.on_save, self.save_button)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_from_runtime(), self.refresh_button)
		self.Bind(wx.EVT_BUTTON, self.on_compact, self.compact_button)
		self.Bind(wx.EVT_BUTTON, self.on_clear, self.clear_button)
		self.refresh_from_runtime()

	def _set_loading_state(self, is_loading, message=None):
		self.enable_checkbox.Enable(not is_loading)
		self.mode_choice.Enable(not is_loading)
		self.max_size_ctrl.Enable(not is_loading)
		self.min_chars_ctrl.Enable(not is_loading and self._selected_mode_key() == "custom")
		self.max_chars_ctrl.Enable(not is_loading and self._selected_mode_key() == "custom")
		self.save_button.Enable(not is_loading)
		self.refresh_button.Enable(not is_loading)
		self.compact_button.Enable(not is_loading)
		self.clear_button.Enable(not is_loading)
		if is_loading:
			self.status_label.SetLabel(message or _("Loading speech cache settings..."))
		self.Layout()

	def _run_busy(self, message, callback):
		busy = wx.BusyInfo(message, parent=self)
		try:
			return callback()
		finally:
			del busy

	def _selected_mode_key(self):
		index = self.mode_choice.GetSelection()
		if index == wx.NOT_FOUND:
			return self._mode_options[0][0]
		return self._mode_options[index][0]

	def _load_into_controls(self, settings, stats):
		self.enable_checkbox.SetValue(bool(settings.get("enabled", True)))
		mode_key = settings.get("mode", self._mode_options[0][0])
		mode_index = 0
		for index, item in enumerate(self._mode_options):
			if item[0] == mode_key:
				mode_index = index
				break
		self.mode_choice.SetSelection(mode_index)
		self.max_size_ctrl.SetValue(int(settings.get("maxSizeMb", 256)))
		self._custom_limits = {
			"min": int(settings.get("customMinChars", settings.get("minChars", 2))),
			"max": int(settings.get("customMaxChars", settings.get("maxChars", 80))),
		}
		self._apply_stats(stats)
		self._update_custom_state()

	def _apply_stats(self, stats):
		cache_available = stats.get("available", True)
		self.clear_button.Enable(cache_available)
		self.compact_button.Enable(cache_available)
		if not stats.get("available", True):
			self.path_label.SetLabel(_("Database: unavailable"))
			self.persistent_size_label.SetLabel(_("Persistent cache size: unavailable"))
			self.persistent_entries_label.SetLabel(_("Persistent cache entries: unavailable"))
			self.hot_size_label.SetLabel(_("Hot cache size: unavailable"))
			self.hot_entries_label.SetLabel(_("Hot cache entries: unavailable"))
			return
		self.path_label.SetLabel(_("Database: {path}").format(path=stats.get("dbPath", "")))
		self.persistent_size_label.SetLabel(
			_("Persistent cache: {size} of {limit}").format(
				size=_format_cache_size(stats.get("sizeBytes", 0)),
				limit=_("{size} MB").format(size=stats.get("settings", {}).get("maxSizeMb", 0)),
			)
		)
		self.persistent_entries_label.SetLabel(
			_("Persistent cache entries: {count}").format(count=stats.get("entryCount", 0))
		)
		self.hot_size_label.SetLabel(
			_("Hot cache: {size}").format(size=_format_cache_size(stats.get("hotSizeBytes", 0)))
		)
		self.hot_entries_label.SetLabel(
			_("Hot cache entries: {count} | TTL: {ttl}s").format(
				count=stats.get("hotEntryCount", 0),
				ttl=stats.get("hotTtlSeconds", 0),
			)
		)

	def _update_custom_state(self):
		mode_key = self._selected_mode_key()
		is_custom = mode_key == "custom"
		if is_custom:
			self.min_chars_ctrl.SetValue(self._custom_limits["min"])
			self.max_chars_ctrl.SetValue(max(self._custom_limits["min"], self._custom_limits["max"]))
		elif mode_key == "short_medium":
			self.min_chars_ctrl.SetValue(2)
			self.max_chars_ctrl.SetValue(180)
		else:
			self.min_chars_ctrl.SetValue(2)
			self.max_chars_ctrl.SetValue(80)
		self.min_chars_ctrl.Enable(is_custom)
		self.max_chars_ctrl.Enable(is_custom)
		if mode_key == "short_ui":
			self.mode_hint.SetLabel(_("Caches short repeated UI speech such as navigation prompts and command feedback."))
		elif mode_key == "short_medium":
			self.mode_hint.SetLabel(_("Extends caching to somewhat longer announcements at the cost of more disk usage."))
		else:
			self.mode_hint.SetLabel(_("Custom mode lets you decide the minimum and maximum utterance lengths that are eligible for caching."))
		self.Layout()

	def _on_custom_limit_change(self, event):
		self._custom_limits["min"] = self.min_chars_ctrl.GetValue()
		self._custom_limits["max"] = max(self._custom_limits["min"], self.max_chars_ctrl.GetValue())
		if self.max_chars_ctrl.GetValue() != self._custom_limits["max"]:
			self.max_chars_ctrl.SetValue(self._custom_limits["max"])
		event.Skip()

	def _collect_settings(self):
		return {
			"enabled": self.enable_checkbox.GetValue(),
			"mode": self._selected_mode_key(),
			"maxSizeMb": self.max_size_ctrl.GetValue(),
			"customMinChars": self._custom_limits["min"],
			"customMaxChars": self._custom_limits["max"],
		}

	def refresh_from_runtime(self):
		self._refresh_generation += 1
		generation = self._refresh_generation
		self._set_loading_state(True, _("Loading speech cache settings..."))

		def _worker():
			try:
				settings = service.get_speech_cache_settings()
				stats = service.get_speech_cache_stats()
			except Exception as error:
				wx.CallAfter(self._finish_refresh, generation, None, None, str(error))
				return
			wx.CallAfter(self._finish_refresh, generation, settings, stats, None)

		thread = threading.Thread(target=_worker, name="MaxLogicXTTSV2CacheLoad", daemon=True)
		thread.start()

	def _finish_refresh(self, generation, settings=None, stats=None, error_message=None):
		if generation != self._refresh_generation:
			return
		if error_message:
			self._set_loading_state(False)
			self.status_label.SetLabel(
				_("Speech cache is currently unavailable.\nReason: {error}").format(error=error_message)
			)
			return
		self._load_into_controls(settings, stats)
		self._set_loading_state(False)
		if stats.get("available", True):
			self.status_label.SetLabel(
				_("Speech cache settings loaded. Persistent cache shows SQLite-backed audio; hot cache shows short-lived helper memory used for quick paragraph repeats.")
			)
		else:
			self.status_label.SetLabel(
				_("Speech cache is currently unavailable.\nReason: {error}").format(error=stats.get("error", _("unknown")))
			)

	def on_save(self, event):
		try:
			payload = self._run_busy(
				_("Saving speech cache settings..."),
				lambda: service.save_speech_cache_settings(self._collect_settings()),
			)
		except Exception as error:
			log.exception("MaxLogic XTTS v2 speech cache settings save failed", exc_info=True)
			gui.messageBox(
				_("Saving speech cache settings failed.\nSee NVDA's log for details.\n{error}").format(error=error),
				_("Speech cache settings failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self._load_into_controls(payload["settings"], payload["stats"])
		self.status_label.SetLabel(_("Speech cache settings saved."))
		gui.messageBox(
			_("Speech cache settings were saved and will apply to new utterances immediately."),
			_("Speech cache settings saved"),
			wx.OK | wx.ICON_INFORMATION,
		)

	def on_clear(self, event):
		response = gui.messageBox(
			_("Do you want to remove all cached speech audio?"),
			_("Clear speech cache?"),
			wx.YES_NO | wx.ICON_WARNING,
		)
		if response != wx.YES:
			return
		try:
			stats = self._run_busy(_("Clearing speech cache..."), service.clear_speech_cache)
		except Exception as error:
			log.exception("MaxLogic XTTS v2 clear speech cache failed", exc_info=True)
			gui.messageBox(
				_("Clearing the speech cache failed.\nSee NVDA's log for details.\n{error}").format(error=error),
				_("Clear speech cache failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self._apply_stats(stats)
		self.status_label.SetLabel(_("Speech cache cleared."))

	def on_compact(self, event):
		try:
			payload = self._run_busy(_("Compacting speech cache..."), service.compact_speech_cache)
		except Exception as error:
			log.exception("MaxLogic XTTS v2 compact speech cache failed", exc_info=True)
			gui.messageBox(
				_("Compacting the speech cache failed.\nSee NVDA's log for details.\n{error}").format(error=error),
				_("Compact speech cache failed"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self._apply_stats(payload["stats"])
		if payload.get("restartRequired"):
			self.status_label.SetLabel(_("Close MaxLogic XTTS v2 or restart NVDA before compacting the cache."))
			gui.messageBox(
				_("Speech cache compaction cannot run while MaxLogic XTTS v2 is the active synth.\nSwitch to another synth or restart NVDA, then try again."),
				_("Speech cache compaction unavailable"),
				wx.OK | wx.ICON_INFORMATION,
			)
			return
		self.status_label.SetLabel(_("Speech cache compacted."))


class MaxLogicVoiceManagerDialog(wx.Dialog):
	def __init__(self):
		super(MaxLogicVoiceManagerDialog, self).__init__(
			parent=gui.mainFrame,
			title=_("MaxLogic XTTS v2 voice manager"),
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
		)
		self.SetSize((900, 620))
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		self.notebook = wx.Notebook(self)
		self.installed_panel = InstalledVoicesPanel(self.notebook, on_change=self.refresh_all)
		self.browse_panel = BrowseVoicesPanel(self.notebook, on_change=self.refresh_all)
		self.extract_panel = ExtractSamplePanel(self.notebook, on_change=self.refresh_all)
		self.cache_panel = SpeechCachePanel(self.notebook)
		self.notebook.AddPage(self.installed_panel, _("Installed"))
		self.notebook.AddPage(self.browse_panel, _("Browse Voices"))
		self.notebook.AddPage(self.extract_panel, _("Extract Sample"))
		self.notebook.AddPage(self.cache_panel, _("Speech Cache"))
		self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_page_changed)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		main_sizer.Add(self.notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
		button_sizer = self.CreateButtonSizer(wx.CLOSE)
		main_sizer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizer(main_sizer)
		self.CentreOnScreen()
		self._log_active_page()

	def refresh_all(self):
		self.installed_panel.refresh_entries()
		self.browse_panel.refresh_inventory_state()
		self.cache_panel.refresh_from_runtime()

	def _describe_active_page(self):
		index = self.notebook.GetSelection()
		if index == wx.NOT_FOUND:
			return None
		label = self.notebook.GetPageText(index)
		page = self.notebook.GetPage(index)
		catalog_name = getattr(page, "_catalog_name", None)
		source_label = None
		if hasattr(page, "active_source_payload"):
			source_payload = page.active_source_payload()
			catalog_name = source_payload.get("catalog")
			source_label = source_payload.get("label")
		return {
			"index": index,
			"label": label,
			"catalog": catalog_name,
			"source": source_label,
		}

	def _log_active_page(self):
		payload = self._describe_active_page()
		if payload is None:
			return
		log.info(
			"MaxLogic XTTS v2 voice manager selected page. label=%s source=%s catalog=%s index=%s",
			payload["label"],
			payload["source"] or "n/a",
			payload["catalog"] or "n/a",
			payload["index"],
		)

	def on_page_changed(self, event):
		self._log_active_page()
		event.Skip()

	def on_close(self, event):
		try:
			self.extract_panel.cleanup()
		except Exception:
			pass
		event.Skip()
