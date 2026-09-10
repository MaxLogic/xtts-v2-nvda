"""Keyboard-first workflow for creating a voice from prepared recordings."""
import ui
import wx
from wx.lib.scrolledpanel import ScrolledPanel

from . import service
from ._ui import StatusText, NamedAccessible, run_busy
from synthDrivers.maxlogic_xtts_v2._voice_presets import CONDITIONING_PRESETS, GENERATION_PRESETS


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
		self.trim = wx.CheckBox(self, label=_("&Trim silence at recording edges (keep a short margin)"))
		sizer.Add(self.trim, 0, wx.ALL, 5)
		sizer.Add(wx.StaticText(self, label=_("Reference conditioning preset")), 0, wx.ALL, 5)
		self.conditioning_preset = wx.Choice(self, choices=[_("Default: 30 / 6 / 6 seconds"), _("Extended: 30 / 12 / 6 seconds"), _("Longer conditioning: 30 / 30 / 6 seconds"), _("Custom")], name=_("Reference conditioning preset"))
		self.conditioning_preset.SetSelection(0)
		sizer.Add(self.conditioning_preset, 0, wx.EXPAND | wx.ALL, 5)
		sizer.Add(wx.StaticText(self, label=_("Speech generation preset (saved with this voice)")), 0, wx.ALL, 5)
		self.generation_preset = wx.Choice(self, choices=[_("XTTS inference defaults"), _("Suggested range: midpoint"), _("Suggested range: lower values"), _("Suggested range: upper values")], name=_("Speech generation preset"))
		self.generation_preset.SetSelection(0)
		sizer.Add(self.generation_preset, 0, wx.EXPAND | wx.ALL, 5)
		self.preset_status = StatusText(self, label="", name=_("Speech generation settings"))
		sizer.Add(self.preset_status, 0, wx.EXPAND | wx.ALL, 5)
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
			control.Bind(wx.EVT_SPINCTRL, lambda event: self.conditioning_preset.SetSelection(3))
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
		self.check_button = wx.Button(self, label=_("Chec&k recordings"))
		self.help_button = wx.Button(self, label=_("&Help: choosing recordings"))
		row = wx.WrapSizer(wx.HORIZONTAL)
		for button in (self.create_button, self.preview_button, self.check_button, self.help_button):
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
		self.check_button.Bind(wx.EVT_BUTTON, self.on_check)
		self.help_button.Bind(wx.EVT_BUTTON, self.on_help)
		self.conditioning_preset.Bind(wx.EVT_CHOICE, self.on_conditioning_preset)
		self.generation_preset.Bind(wx.EVT_CHOICE, self.on_generation_preset)
		self.on_generation_preset(None)

	def on_conditioning_preset(self, event):
		index = self.conditioning_preset.GetSelection()
		if index < len(CONDITIONING_PRESETS):
			for control, value in zip(self.lengths.values(), CONDITIONING_PRESETS[index][1]):
				control.SetValue(value)
			self.say(_("Reference conditioning lengths updated. Normalization and trimming are unchanged."))

	def on_generation_preset(self, event):
		values = GENERATION_PRESETS[self.generation_preset.GetSelection()][1]
		self.preset_status.SetLabel(_("Temperature {temperature}; top p {top_p}; top k {top_k}; repetition penalty {repetition_penalty}; speed {speed}. These control generated speech, not cloning quality.").format(**values))
		if event:
			ui.message(self.preset_status.GetValue())

	def show_text(self, title, text):
		with wx.Dialog(self, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER) as dialog:
			sizer = wx.BoxSizer(wx.VERTICAL)
			content = wx.TextCtrl(dialog, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(640, 410), name=title)
			content.SetAccessible(NamedAccessible(content))
			sizer.Add(content, 1, wx.EXPAND | wx.ALL, 10)
			sizer.Add(dialog.CreateButtonSizer(wx.OK), 0, wx.ALIGN_RIGHT | wx.ALL, 10)
			dialog.SetSizerAndFit(sizer)
			dialog.SetEscapeId(wx.ID_OK)
			content.SetInsertionPoint(0)
			content.SetFocus()
			dialog.ShowModal()

	def on_help(self, event):
		self.show_text(_("Choosing XTTS voice recordings"), _(
			"Use one speaker, a quiet room, a steady microphone distance, and natural speech in the style you want. Avoid music, echo, overlapping voices, clipping and heavy noise removal.\n\n"
			"Start with roughly 6 to 15 seconds of clear speech per recording. This is a practical starting point, not a guaranteed optimum. Upstream recommends more than 3 seconds; its short-chunk guard is 0.33 seconds, not a quality target.\n\n"
			"Several clean recordings can add useful variety, but more is not always better. A good single recording can beat several noisy or inconsistent ones. XTTS averages the speaker embeddings. GPT conditioning uses only the selected total duration from the joined recordings, in list order. With the default 6 seconds, later recordings may not contribute to GPT conditioning. Use Extended or Longer conditioning to try more audio.\n\n"
			"Remove long silence at the edges rather than adding it. Optional trimming works on temporary copies, keeps about 100 milliseconds of margin, and preserves internal pauses. Quiet consonants can still be trimmed, so use Extract Sample for manual control.\n\n"
			"WAV or FLAC avoids further lossy encoding. Supported compressed audio can also work. Mono is convenient, but stereo is accepted and mixed down; avoid stereo tracks containing different speakers. XTTS resamples internally to 22050 Hz for conditioning and 16000 Hz for its speaker encoder, then produces 24000 Hz speech. You do not need to convert 48000 Hz recordings to 24000 Hz first. Conversion cannot restore quality already lost.\n\n"
			"Check recordings reports duration, channels and sample rate without loading the model. If decoding fails, convert the source to PCM WAV or FLAC with your audio editor.\n\n"
			"Conditioning presets set maximum seconds per recording, total conditioning seconds and chunk seconds. Speech generation presets set temperature, top p, top k, repetition penalty and speed. Suggested presets are experiments, not proven improvements. A repetition penalty of 2 is lower than this XTTS inference API's default of 10. The speed multiplier combines with NVDA's rate.\n\n"
			"Create voice saves conditioning in the profile. It is reused for new speech; the model still needs to load to generate new text. A matching saved preview WAV plays without loading the model. Changing a preset here applies to the next voice you create; it does not edit an existing profile."
		))
		self.help_button.SetFocus()

	def on_check(self, event):
		if not self.paths:
			self.say(_("Add reference recordings first."))
			return
		paths = list(self.paths)
		def check():
			lines = []
			for path in paths:
				try:
					info = service.probe_audio_source(path)
					line = _("{path}: {duration:.1f} seconds, {channels} channels, {rate} Hz.").format(path=path, duration=info["durationSeconds"], channels=info["channels"], rate=info["sampleRate"])
					if info["durationSeconds"] <= 3:
						line += _(" Short reference: consider a longer clear recording.")
					lines.append(line)
				except Exception as error:
					lines.append(_("{path}: could not decode. Try converting to PCM WAV or FLAC. {error}").format(path=path, error=error))
			return "\n\n".join(lines) + _("\n\nXTTS handles mono conversion and resampling internally. These checks do not assess background noise or voice quality.")
		result = run_busy(self, _("Checking reference recording formats..."), check)
		self.show_text(_("Reference recording details"), result)
		self.check_button.SetFocus()

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
		options["trim_silence"] = self.trim.GetValue()
		synthesis_settings = dict(GENERATION_PRESETS[self.generation_preset.GetSelection()][1])
		service.stop_preview()
		try:
			record = run_busy(self, _("Creating voice conditioning. Loading the model may take tens of seconds..."),
				lambda: service.clone_voice(paths, name, language, options, synthesis_settings))
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
