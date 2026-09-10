"""Keyboard-first workflow for creating a voice from prepared recordings."""
import ui
import wx
from wx.lib.scrolledpanel import ScrolledPanel

from . import service
from ._ui import StatusText, run_busy


class CloneVoicePanel(ScrolledPanel):
	def __init__(self, parent, on_change):
		super().__init__(parent)
		self.on_change = on_change
		self.paths = []
		self.record = None
		self.playing = False
		sizer = wx.BoxSizer(wx.VERTICAL)
		help_text = wx.StaticText(self, label=_("Add clear recordings of the same speaker, without music or other voices. Use Extract Sample to trim longer recordings first."))
		help_text.Wrap(750)
		sizer.Add(help_text, 0, wx.EXPAND | wx.ALL, 5)
		sizer.Add(wx.StaticText(self, label=_("Reference recordings")), 0, wx.ALL, 5)
		self.references = wx.ListBox(self, style=wx.LB_EXTENDED, size=(-1, 100), name=_("Reference recordings"))
		sizer.Add(self.references, 0, wx.EXPAND | wx.ALL, 5)
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.add_button = wx.Button(self, label=_("&Add recordings..."))
		self.remove_button = wx.Button(self, label=_("&Remove selected"))
		self.remove_button.Disable()
		for button in (self.add_button, self.remove_button):
			row.Add(button, 0, wx.ALL, 5)
		sizer.Add(row)
		sizer.Add(wx.StaticText(self, label=_("Voice &name")), 0, wx.ALL, 5)
		self.voice_name = wx.TextCtrl(self, name=_("Voice name"))
		sizer.Add(self.voice_name, 0, wx.EXPAND | wx.ALL, 5)
		sizer.Add(wx.StaticText(self, label=_("Default &language")), 0, wx.ALL, 5)
		self.languages = service.get_preview_language_options()
		self.language = wx.Choice(self, choices=[label for key, label in self.languages], name=_("Default language"))
		self.language.SetSelection(next((i for i, (key, label) in enumerate(self.languages) if key == "en"), 0))
		sizer.Add(self.language, 0, wx.ALL, 5)
		self.normalize = wx.CheckBox(self, label=_("Normalize reference &volume"))
		sizer.Add(self.normalize, 0, wx.ALL, 5)
		self.advanced_toggle = wx.CheckBox(self, label=_("Show advanced &settings"))
		sizer.Add(self.advanced_toggle, 0, wx.ALL, 5)
		self.advanced = wx.Panel(self)
		advanced_sizer = wx.BoxSizer(wx.VERTICAL)
		self.lengths = {}
		for key, label, value in (
			("max_ref_length", _("Maximum seconds per recording"), 30),
			("gpt_cond_len", _("Total conditioning seconds"), 6),
			("gpt_cond_chunk_len", _("Conditioning chunk seconds"), 6),
		):
			advanced_sizer.Add(wx.StaticText(self.advanced, label=label), 0, wx.ALL, 3)
			control = wx.SpinCtrl(self.advanced, min=1, max=120, initial=value, name=label)
			self.lengths[key] = control
			advanced_sizer.Add(control, 0, wx.ALL, 3)
		note = wx.StaticText(self.advanced, label=_("Defaults: 30, 6 and 6 seconds. Conditioning uses the joined recordings; chunk length cannot exceed total conditioning length. Longer settings are not always better."))
		note.Wrap(730)
		advanced_sizer.Add(note, 0, wx.EXPAND | wx.ALL, 3)
		self.advanced.SetSizer(advanced_sizer)
		self.advanced.Hide()
		sizer.Add(self.advanced, 0, wx.EXPAND | wx.ALL, 5)
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.create_button = wx.Button(self, label=_("&Create voice"))
		self.preview_button = wx.Button(self, label=_("&Play created voice sample"))
		self.preview_button.Disable()
		for button in (self.create_button, self.preview_button):
			row.Add(button, 0, wx.ALL, 5)
		sizer.Add(row)
		self.status = StatusText(self, label=_("Add recordings and enter a unique voice name. Existing voices will be kept."), name=_("Cloning status"))
		sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 5)
		self.SetSizer(sizer)
		self.SetupScrolling(scroll_x=False)
		self.add_button.Bind(wx.EVT_BUTTON, self.on_add)
		self.remove_button.Bind(wx.EVT_BUTTON, self.on_remove)
		self.references.Bind(wx.EVT_LISTBOX, lambda event: self.remove_button.Enable(bool(self.references.GetSelections())))
		self.advanced_toggle.Bind(wx.EVT_CHECKBOX, self.on_advanced)
		self.create_button.Bind(wx.EVT_BUTTON, self.on_create)
		self.preview_button.Bind(wx.EVT_BUTTON, self.on_preview)

	def say(self, message):
		self.status.SetLabel(message)
		ui.message(message)

	def on_advanced(self, event):
		self.advanced.Show(self.advanced_toggle.GetValue())
		self.Layout()
		self.SetupScrolling(scroll_x=False, scrollToTop=False)

	def on_add(self, event):
		with wx.FileDialog(self, _("Choose reference recordings"), wildcard=_("Audio files (*.wav;*.mp3;*.flac;*.ogg)|*.wav;*.mp3;*.flac;*.ogg"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			for path in dialog.GetPaths():
				if path not in self.paths:
					self.paths.append(path)
		self.references.Set(self.paths)
		self.remove_button.Disable()
		self.references.SetFocus()
		self.say(_("{count} reference recordings added.").format(count=len(self.paths)))

	def on_remove(self, event):
		for index in reversed(self.references.GetSelections()):
			del self.paths[index]
		self.references.Set(self.paths)
		self.remove_button.Disable()
		self.references.SetFocus()

	def on_create(self, event):
		name = self.voice_name.GetValue().strip()
		if not self.paths or not name:
			self.say(_("Add at least one recording and enter a voice name."))
			(self.voice_name if self.paths else self.add_button).SetFocus()
			return
		paths = list(self.paths)
		language = self.languages[self.language.GetSelection()][0]
		options = {key: control.GetValue() for key, control in self.lengths.items()}
		options["sound_norm_refs"] = self.normalize.GetValue()
		service.stop_preview()
		try:
			record = run_busy(self, _("Creating voice conditioning. Loading the model may take tens of seconds..."),
				lambda: service.clone_voice(paths, name, language, options))
		except Exception as error:
			self.say(_("Voice creation failed: {error}").format(error=error))
			self.voice_name.SetFocus()
			return
		self.record = record
		self.preview_button.Enable()
		self.on_change()
		self.say(_("Created {name}. The voice is in Installed. You can now play its sample.").format(name=record.display_name))
		try:
			run_busy(self, _("Refreshing available voices..."), lambda: service.refresh_active_synth(reason="voice-clone"))
		except Exception:
			self.say(_("The voice was created. Restart NVDA to refresh the active synthesizer's voice list."))
		self.preview_button.SetFocus()

	def on_preview(self, event):
		if self.playing:
			service.stop_preview()
			self.say(_("Playback stopped. Finishing the saved sample in the background..."))
			return
		if not self.record:
			return
		self.playing = True
		self.preview_button.SetLabel(_("Sto&p sample"))
		self.create_button.Disable()
		self.say(_("Preparing the created voice sample..."))
		def complete(status, error=None):
			if not self or self.IsBeingDeleted():
				return
			self.playing = False
			self.create_button.Enable()
			self.preview_button.SetLabel(_("&Play created voice sample"))
			self.say(_("Sample failed: {error}").format(error=error) if error else (_("Sample finished.") if status == "completed" else _("Sample stopped.")))
		service.play_installed_voice_sample(self.record, on_complete=complete)
