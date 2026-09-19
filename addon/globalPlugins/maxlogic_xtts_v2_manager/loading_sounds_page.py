"""The Loading Sounds page: sounds for when the speech engine starts loading and when it is ready."""
import os

import gui
from logHandler import log
import nvwave
import ui
import wx

from . import service
from ._ui import StatusText


PRODUCT = "XTTS"


class LoadingSoundsPanel(wx.Panel):
	def __init__(self, parent):
		super(LoadingSoundsPanel, self).__init__(parent)
		self._settings = service.load_sound_settings()
		self._controls = {}
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		main_sizer.Add(wx.StaticText(self, label=_(
			"{product} takes a while to load. These sounds tell you when it starts loading, that it is still loading, and when it can speak. "
			"Changes apply the next time {product} loads."
		).format(product=PRODUCT)), 0, wx.ALL, 5)
		sounds = (
			(service.LOADING_SOUND, _("Loading sound"), _("Play a sound when {product} starts &loading"),
				_("&Choose file..."), _("Use &default sound"), _("&Play")),
			(service.WAITING_SOUND, _("Still loading sound"), _("Repeat a sound &while {product} is still loading"),
				_("Ch&oose file..."), _("Use defa&ult sound"), _("Pl&ay")),
			(service.READY_SOUND, _("Ready sound"), _("Play a sound when {product} is &ready"),
				_("C&hoose file..."), _("Use d&efault sound"), _("Pla&y")),
		)
		for kind, title, check_label, choose_label, default_label, play_label in sounds:
			box = wx.StaticBoxSizer(wx.VERTICAL, self, title)
			box_parent = box.GetStaticBox()
			checkbox = wx.CheckBox(box_parent, label=check_label.format(product=PRODUCT))
			box.Add(checkbox, 0, wx.ALL, 5)
			file_row = wx.BoxSizer(wx.HORIZONTAL)
			file_row.Add(wx.StaticText(box_parent, label=_("Sound file")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
			file_text = wx.TextCtrl(box_parent, style=wx.TE_READONLY)
			file_text.SetName(_("{sound} file").format(sound=title))
			file_row.Add(file_text, 1, wx.EXPAND)
			box.Add(file_row, 0, wx.EXPAND | wx.ALL, 5)
			buttons = wx.BoxSizer(wx.HORIZONTAL)
			choose_button = wx.Button(box_parent, label=choose_label)
			default_button = wx.Button(box_parent, label=default_label)
			play_button = wx.Button(box_parent, label=play_label)
			for button in (choose_button, default_button, play_button):
				buttons.Add(button, 0, wx.ALL, 5)
			box.Add(buttons, 0)
			if kind == service.WAITING_SOUND:
				interval_row = wx.BoxSizer(wx.HORIZONTAL)
				interval_row.Add(wx.StaticText(box_parent, label=_("Repeat e&very (seconds)")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
				low, high = service.WAITING_INTERVAL_RANGE
				self.interval_ctrl = wx.SpinCtrl(box_parent, min=low, max=high, initial=self._settings[kind]["intervalSeconds"])
				self.interval_ctrl.SetName(_("Repeat every (seconds)"))
				interval_row.Add(self.interval_ctrl, 0)
				box.Add(interval_row, 0, wx.ALL, 5)
				self.interval_ctrl.Bind(wx.EVT_SPINCTRL, lambda event: self.on_interval())
			main_sizer.Add(box, 0, wx.EXPAND | wx.ALL, 5)
			self._controls[kind] = (title, checkbox, file_text, default_button)
			checkbox.Bind(wx.EVT_CHECKBOX, lambda event, kind=kind: self.on_toggle(kind))
			choose_button.Bind(wx.EVT_BUTTON, lambda event, kind=kind: self.on_choose(kind))
			default_button.Bind(wx.EVT_BUTTON, lambda event, kind=kind: self.on_default(kind))
			play_button.Bind(wx.EVT_BUTTON, lambda event, kind=kind: self.on_play(kind))
		self.status_label = StatusText(self)
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
		self.SetSizer(main_sizer)
		for kind in self._controls:
			self._show(kind)

	def _show(self, kind):
		__, checkbox, file_text, default_button = self._controls[kind]
		entry = self._settings[kind]
		checkbox.SetValue(entry["enabled"])
		if entry["path"]:
			file_text.ChangeValue(entry["path"])
		else:
			file_text.ChangeValue(_("The sound that comes with the add-on"))
		default_button.Enable(bool(entry["path"]))
		if kind == service.WAITING_SOUND:
			self.interval_ctrl.SetValue(entry["intervalSeconds"])

	def _save(self, kind, message):
		try:
			self._settings = service.save_sound_settings(self._settings)
		except OSError as error:
			log.warning("MaxLogic XTTS v2 loading sound settings could not be saved", exc_info=True)
			gui.messageBox(_("The setting could not be saved. {error}").format(error=error),
				_("Loading sounds"), wx.OK | wx.ICON_ERROR, self)
			self._settings = service.load_sound_settings()
			self._show(kind)
			return
		self._show(kind)
		self.status_label.SetLabel(message)

	def on_toggle(self, kind):
		title, checkbox = self._controls[kind][:2]
		self._settings[kind]["enabled"] = checkbox.GetValue()
		if checkbox.GetValue():
			self._save(kind, _("{sound} on.").format(sound=title))
		else:
			self._save(kind, _("{sound} off.").format(sound=title))

	def on_interval(self):
		seconds = self.interval_ctrl.GetValue()
		self._settings[service.WAITING_SOUND]["intervalSeconds"] = seconds
		self._save(service.WAITING_SOUND, _("Still loading sound repeats every {seconds} seconds.").format(seconds=seconds))

	def on_choose(self, kind):
		title = self._controls[kind][0]
		current = self._settings[kind]["path"]
		with wx.FileDialog(self, _("Choose the {sound}").format(sound=title.lower()),
				defaultDir=os.path.dirname(current) if current else "",
				wildcard=_("WAV files (*.wav)|*.wav"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			path = dialog.GetPath()
		if not service.is_playable_wave(path):
			gui.messageBox(_("NVDA can play only uncompressed WAV files. Choose another file."),
				_("Unsupported sound file"), wx.OK | wx.ICON_ERROR, self)
			return
		self._settings[kind]["path"] = path
		message = _("{sound} set to {name}.").format(sound=title, name=os.path.basename(path))
		self._save(kind, message)
		ui.message(message)

	def on_default(self, kind):
		title = self._controls[kind][0]
		self._settings[kind]["path"] = ""
		message = _("{sound} set to the sound that comes with the add-on.").format(sound=title)
		self._save(kind, message)
		ui.message(message)
		# The button just pressed is now disabled, so keep focus on the page.
		self._controls[kind][1].SetFocus()

	def on_play(self, kind):
		path = self._settings[kind]["path"]
		if not path or not os.path.isfile(path):
			path = service.default_sound_path(kind)
		try:
			nvwave.playWaveFile(path, asynchronous=True)
		except Exception as error:
			log.warning("MaxLogic XTTS v2 could not play %s", path, exc_info=True)
			self.status_label.SetLabel(_("The sound could not be played. {error}").format(error=error))
