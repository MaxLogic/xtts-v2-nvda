"""Keyboard-first workflow for creating a voice from prepared recordings."""
import ui
import wx

from . import service
from ._ui import StatusText, NamedAccessible, run_busy, ButtonBusy
from synthDrivers.maxlogic_xtts_v2._reference_coverage import balanced_style_seconds, style_seconds
from synthDrivers.maxlogic_xtts_v2._voice_presets import CONDITIONING_PRESETS, GENERATION_PRESETS


class CloneVoicePanel(wx.Panel):
	def __init__(self, parent, on_change):
		super().__init__(parent)
		self.on_change = on_change
		self.paths = []
		self.record = None
		self.playing = False
		self._closed = False
		self._preview_busy = None
		self._reference_playing = False
		self._reference_generation = 0
		sizer = wx.BoxSizer(wx.VERTICAL)
		help_text = wx.StaticText(self, label=_("Add clear recordings of the same speaker, without music or other voices. Use Extract Sample to trim longer recordings first."))
		help_text.Wrap(750)
		sizer.Add(help_text, 0, wx.EXPAND | wx.ALL, 5)
		sizer.Add(wx.StaticText(self, label=_("Reference recordings")), 0, wx.ALL, 5)
		self.references = wx.ListBox(self, style=wx.LB_EXTENDED, size=(-1, 60), name=_("Reference recordings"))
		sizer.Add(self.references, 0, wx.EXPAND | wx.ALL, 5)
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.add_button = wx.Button(self, label=_("&Add recordings..."))
		self.remove_button = wx.Button(self, label=_("&Remove selected"))
		self.remove_button.Disable()
		for button in (self.add_button, self.remove_button):
			row.Add(button, 0, wx.ALL, 5)
		sizer.Add(row)
		row_reference = row
		playback_row = wx.BoxSizer(wx.HORIZONTAL)
		self.reference_play_button = wx.Button(self, label=_("Play selected record&ing"))
		self.reference_play_button.Disable()
		playback_row.Add(self.reference_play_button, 0, wx.ALL, 5)
		sizer.Add(playback_row)
		self.check_button = wx.Button(self, label=_("Chec&k recordings"))
		self.help_button = wx.Button(self, label=_("&Help: choosing recordings"))
		settings = wx.FlexGridSizer(cols=2, hgap=8, vgap=5)
		settings.AddGrowableCol(1)
		settings.Add(wx.StaticText(self, label=_("Default &language")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.languages = service.get_preview_language_options()
		self.language = wx.Choice(self, choices=[label for key, label in self.languages], name=_("Default language"))
		self.language.SetSelection(next((i for i, (key, label) in enumerate(self.languages) if key == "en"), 0))
		settings.Add(self.language, 0, wx.EXPAND)
		settings.Add(wx.StaticText(self, label=_("Reference conditioning preset")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.conditioning_preset = wx.Choice(self, choices=[_("XTTS model config (recommended): 30 / 30 / 4 seconds"), _("Function defaults: 30 / 6 / 6 seconds"), _("Extended: 30 / 12 / 6 seconds"), _("Longer conditioning: 30 / 30 / 6 seconds"), _("Custom")], name=_("Reference conditioning preset"))
		self.conditioning_preset.SetSelection(0)
		settings.Add(self.conditioning_preset, 0, wx.EXPAND)
		settings.Add(wx.StaticText(self, label=_("Speech generation preset")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.generation_preset = wx.Choice(self, choices=[_("XTTS model config (recommended)"), _("XTTS inference function defaults"), _("Suggested range: midpoint"), _("Suggested range: lower values"), _("Suggested range: upper values")], name=_("Speech generation preset"))
		self.generation_preset.SetSelection(0)
		settings.Add(self.generation_preset, 0, wx.EXPAND)
		sizer.Add(settings, 0, wx.EXPAND | wx.ALL, 5)
		self.normalize = wx.CheckBox(self, label=_("Normalize reference &volume"))
		preparation = wx.BoxSizer(wx.HORIZONTAL)
		preparation.Add(self.normalize, 0, wx.RIGHT, 15)
		self.trim = wx.CheckBox(self, label=_("Trim silence at recording &edges (keep a short margin)"))
		preparation.Add(self.trim)
		sizer.Add(preparation, 0, wx.ALL, 5)
		self.balance = wx.CheckBox(self, label=_("&Balance speech style across all recordings"))
		self.balance.SetValue(True)
		sizer.Add(self.balance, 0, wx.ALL, 5)
		self.preset_status = StatusText(self, label="", name=_("Speech generation settings"))
		self.preset_status.SetMinSize((-1, 42))
		sizer.Add(self.preset_status, 0, wx.EXPAND | wx.ALL, 5)
		self.advanced_toggle = wx.CheckBox(self, label=_("Show advanced settin&gs"))
		sizer.Add(self.advanced_toggle, 0, wx.ALL, 5)
		self.advanced = wx.Panel(self)
		advanced_sizer = wx.BoxSizer(wx.VERTICAL)
		length_grid = wx.FlexGridSizer(cols=2, hgap=8, vgap=5)
		self.lengths = {}
		for (key, label), value in zip((
			("max_ref_length", _("Maximum seconds per recording")),
			("gpt_cond_len", _("Total conditioning seconds")),
			("gpt_cond_chunk_len", _("Conditioning chunk seconds")),
		), CONDITIONING_PRESETS[0][1]):
			length_grid.Add(wx.StaticText(self.advanced, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
			control = wx.SpinCtrl(self.advanced, min=1, max=120, initial=value, name=label)
			self.lengths[key] = control
			control.Bind(wx.EVT_SPINCTRL, lambda event: self.conditioning_preset.SetSelection(len(CONDITIONING_PRESETS)))
			length_grid.Add(control)
		advanced_sizer.Add(length_grid, 0, wx.ALL, 3)
		note = wx.StaticText(self.advanced, label=_("Recommended: 30, 30 and 4 seconds, as in the XTTS model config. The model was trained on 3 to 6 second chunks. Chunk length cannot exceed total conditioning length. Very long totals average out expressive variation."))
		note.Wrap(730)
		advanced_sizer.Add(note, 0, wx.EXPAND | wx.ALL, 3)
		self.advanced.SetSizer(advanced_sizer)
		self.advanced.Hide()
		sizer.Add(self.advanced, 0, wx.EXPAND | wx.ALL, 5)
		sample_header = wx.BoxSizer(wx.HORIZONTAL)
		sample_header.Add(wx.StaticText(self, label=_("Sample &text")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
		self.sample_text = wx.TextCtrl(self, value=service.get_sample_text(self.selected_language()),
			style=wx.TE_MULTILINE, size=(-1, 55), name=_("Sample text"))
		self.sample_text.SetAccessible(NamedAccessible(self.sample_text))
		self.reset_text_button = wx.Button(self, label=_("Reset to &default text"))
		sample_header.Add(self.reset_text_button)
		sizer.Add(sample_header, 0, wx.ALL, 5)
		sizer.Add(self.sample_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
		self.create_button = wx.Button(self, label=_("Clo&ne for testing"))
		self.preview_button = wx.Button(self, label=_("&Play sample"))
		self.preview_button.Disable()
		self.save_button = wx.Button(self, label=_("&Save voice..."))
		self.save_button.Disable()
		for button in (self.check_button, self.help_button):
			row_reference.Add(button, 0, wx.ALL, 5)
		for buttons in ((self.create_button, self.preview_button, self.save_button),):
			row = wx.BoxSizer(wx.HORIZONTAL)
			for button in buttons:
				row.Add(button, 0, wx.ALL, 5)
			sizer.Add(row)
		self.status = StatusText(self, label=_("Add recordings, then clone for testing. Nothing is installed until you choose Save voice."), name=_("Cloning status"))
		self.status.SetMinSize((-1, 42))
		sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 5)
		self.SetSizer(sizer)
		self.save_button.Bind(wx.EVT_BUTTON, self.on_save)
		self.reset_text_button.Bind(wx.EVT_BUTTON, self.on_reset_text)
		self.language.Bind(wx.EVT_CHOICE, self.on_language)
		self.add_button.Bind(wx.EVT_BUTTON, self.on_add)
		self.remove_button.Bind(wx.EVT_BUTTON, self.on_remove)
		self.references.Bind(wx.EVT_LISTBOX, self.on_reference_selection)
		self.reference_play_button.Bind(wx.EVT_BUTTON, self.on_reference_play)
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
			"Several clean recordings can add useful variety, but more is not always better. A good single recording can beat several noisy or inconsistent ones. XTTS averages the speaker embeddings. Stock XTTS GPT style conditioning uses only the selected total duration from the start of the joined recordings, in list order, so later recordings may add no speaking style. Balance speech style (on by default) instead shares that duration across all recordings, taking each share from the middle of the recording; the order then no longer matters. Use Extended or Longer conditioning to give each recording more style time.\n\n"
			"Remove long silence at the edges rather than adding it. Optional trimming works on temporary copies, keeps about 100 milliseconds of margin, and preserves internal pauses. Quiet consonants can still be trimmed, so use Extract Sample for manual control.\n\n"
			"WAV or FLAC avoids further lossy encoding. Supported compressed audio can also work. Mono is convenient, but stereo is accepted and mixed down; avoid stereo tracks containing different speakers. XTTS resamples internally to 22050 Hz for conditioning and 16000 Hz for its speaker encoder, then produces 24000 Hz speech. You do not need to convert 48000 Hz recordings to 24000 Hz first. Conversion cannot restore quality already lost.\n\n"
			"Select exactly one reference and use Play selected recording to hear the original input. The same button stops it. Playback does not apply cloning normalization or trimming.\n\n"
			"Check recordings reports duration, channels, sample rate and estimated GPT style contribution for the current balancing setting without loading the model. Zero style contribution still allows speaker identity contribution. When silence trimming is enabled, the contribution estimate uses original durations and later recordings may contribute more after trimming. Before cloning, a warning lets you review references estimated to contribute no GPT style. No recordings or settings are changed automatically. If decoding fails, convert the source to PCM WAV or FLAC with your audio editor.\n\n"
			"Conditioning presets set maximum seconds per recording, total conditioning seconds and chunk seconds. Speech generation presets set temperature, top p, top k, repetition penalty and speed. Suggested presets are experiments, not proven improvements. The XTTS model config uses repetition penalty 5 with 30 seconds of conditioning in 4 second chunks; Coqui's high-level synthesis uses these values. The bare inference function defaults use 6 seconds in one chunk and repetition penalty 10. A repetition penalty of 2 is lower than both. The speed multiplier combines with NVDA's rate.\n\n"
			"Clone for testing prepares temporary conditioning. Play sample lets you try your own text. Save voice asks for a name and explicitly installs the result; confirm replacement if that name already exists. Closing the manager discards an unsaved test. Saved conditioning is reused for new speech; the model still needs to load to generate new text. A matching saved preview WAV plays without loading the model. Changing a preset here applies to the next voice you create; it does not edit an existing profile."
		))
		self.help_button.SetFocus()

	def on_check(self, event):
		if not self.paths:
			self.say(_("Add reference recordings first."))
			return
		paths = list(self.paths)
		options = self.reference_options()
		def check():
			lines = []
			durations = []
			for path in paths:
				try:
					info = service.probe_audio_source(path)
					durations.append(info["durationSeconds"])
					line = _("{path}: {duration:.1f} seconds, {channels} channels, {rate} Hz.").format(path=path, duration=info["durationSeconds"], channels=info["channels"], rate=info["sampleRate"])
					if info["durationSeconds"] <= 3:
						line += _(" Short reference: consider a longer clear recording.")
					lines.append(line)
				except Exception as error:
					durations.append(None)
					lines.append(_("{path}: could not decode. Try converting to PCM WAV or FLAC. {error}").format(path=path, error=error))
			coverage, unused = self.reference_coverage(paths, durations, options)
			return "\n\n".join(lines) + "\n\n" + coverage + _("\n\nXTTS handles mono conversion and resampling internally. These checks do not assess background noise or voice quality.")
		result = run_busy(self, _("Checking reference recording formats..."), check, button=self.check_button, completion_message=_("Recordings checked."))
		self.show_text(_("Reference recording details"), result)
		self.check_button.SetFocus()

	def reference_options(self):
		options = {key: control.GetValue() for key, control in self.lengths.items()}
		options["sound_norm_refs"] = self.normalize.GetValue()
		options["trim_silence"] = self.trim.GetValue()
		options["balance_style"] = self.balance.GetValue()
		return options

	@staticmethod
	def reference_coverage(paths, durations, options):
		if options["balance_style"]:
			amounts = balanced_style_seconds(durations, options["max_ref_length"], options["gpt_cond_len"])
			lines = [_("GPT style is balanced: the {total} seconds are shared across recordings, taken from the middle of each, capped at {maximum} seconds per recording. All valid recordings also contribute to speaker identity.").format(maximum=options["max_ref_length"], total=options["gpt_cond_len"])]
		else:
			amounts = style_seconds(durations, options["max_ref_length"], options["gpt_cond_len"], options["gpt_cond_chunk_len"])
			lines = [_("GPT style uses the recordings in list order, capped at {maximum} seconds per recording and {total} seconds in total. All valid recordings still contribute to speaker identity.").format(maximum=options["max_ref_length"], total=options["gpt_cond_len"])]
		if options["trim_silence"]:
			lines.append(_("Estimate before silence trimming: trimming can shorten recordings and allow later recordings to contribute. Exact contributions are not known before preparation."))
		else:
			lines.append(_("Duration estimate; decoding and resampling can affect boundaries. GPT chunks shorter than 0.33 seconds are skipped."))
		unused = []
		for index, (path, amount) in enumerate(zip(paths, amounts), 1):
			if amount is None:
				lines.append(_("{index}. {path}: contribution unknown because a recording could not be checked.").format(index=index, path=path))
			elif amount < 0.005:
				unused.append(path)
				lines.append(_("{index}. {path}: WARNING: estimated 0 seconds of GPT style; still used for speaker identity.").format(index=index, path=path))
			else:
				lines.append(_("{index}. {path}: approximately {seconds:.2f} seconds of GPT style.").format(index=index, path=path, seconds=amount))
		return "\n\n".join(lines), unused

	def on_reference_selection(self, event=None):
		selected = self.references.GetSelections()
		self.remove_button.Enable(bool(selected))
		self.reference_play_button.Enable(self._reference_playing or (len(selected) == 1 and not self.playing))

	def stop_reference(self):
		if not self._reference_playing:
			return
		self._reference_generation += 1
		self._reference_playing = False
		service.stop_preview()
		if not self._closed:
			self.reference_play_button.SetLabel(_("Play selected record&ing"))
			self.on_reference_selection()

	def on_reference_play(self, event):
		if self._reference_playing:
			self.stop_reference()
			self.say(_("Recording playback stopped."))
			return
		selected = self.references.GetSelections()
		if len(selected) != 1 or self.playing:
			self.say(_("Select one reference recording to play."))
			return
		path = self.paths[selected[0]]
		try:
			info = run_busy(self, _("Opening reference recording..."), lambda: service.probe_audio_source(path), button=self.reference_play_button, completion_message="")
		except Exception as error:
			self.say(_("Could not play recording: {error}").format(error=error))
			return
		self._reference_generation += 1
		generation = self._reference_generation
		self._reference_playing = True
		self.reference_play_button.SetLabel(_("Stop record&ing"))
		self.say(_("Preparing recording playback..."))
		def current():
			return not self._closed and bool(self) and not self.IsBeingDeleted() and generation == self._reference_generation
		def started():
			if current():
				self.say(_("Playing reference recording: {path}").format(path=path))
		def complete(status, error=None):
			if not current():
				return
			self._reference_generation += 1
			self._reference_playing = False
			self.reference_play_button.SetLabel(_("Play selected record&ing"))
			self.on_reference_selection()
			self.say(_("Recording playback failed: {error}").format(error=error) if error else (_("Recording finished.") if status == "completed" else _("Recording stopped.")))
		try:
			service.play_audio_source_segment(path, 0, info["durationSeconds"] * 1000, on_complete=complete, on_playback_started=started)
		except Exception as error:
			complete("error", str(error))

	def say(self, message):
		self.status.SetLabel(message)
		ui.message(message)

	def on_advanced(self, event):
		self.advanced.Show(self.advanced_toggle.GetValue())
		self.Layout()
		dialog = wx.GetTopLevelParent(self)
		if self.advanced_toggle.GetValue():
			display_index = wx.Display.GetFromWindow(dialog)
			area = wx.Display(display_index if display_index != wx.NOT_FOUND else 0).GetClientArea()
			needed = self.GetSizer().CalcMin().height + dialog.GetSize().height - self.GetSize().height
			if needed > area.height:
				# On a smaller display, use a fitted native dialog rather than clipping
				# controls or bringing back the cloning page's scrollbox.
				position = next(i for i, item in enumerate(self.GetSizer().GetChildren()) if item.GetWindow() is self.advanced)
				self.GetSizer().Detach(self.advanced)
				with wx.Dialog(self, title=_("Advanced cloning settings"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER) as settings:
					sizer = wx.BoxSizer(wx.VERTICAL)
					self.advanced.Reparent(settings)
					self.advanced.Show()
					sizer.Add(self.advanced, 0, wx.EXPAND | wx.ALL, 10)
					sizer.Add(settings.CreateButtonSizer(wx.OK), 0, wx.ALIGN_RIGHT | wx.ALL, 10)
					settings.SetSizerAndFit(sizer)
					settings.SetEscapeId(wx.ID_OK)
					try:
						settings.ShowModal()
					finally:
						sizer.Detach(self.advanced)
						self.advanced.Hide()
						self.advanced.Reparent(self)
						self.GetSizer().Insert(position, self.advanced, 0, wx.EXPAND | wx.ALL, 5)
				self.advanced_toggle.SetValue(False)
				self.advanced_toggle.SetFocus()
		if hasattr(dialog, "fit_active_page"):
			dialog.fit_active_page()

	def on_add(self, event):
		with wx.FileDialog(self, _("Choose reference recordings"), wildcard=_("Audio files (*.wav;*.mp3;*.flac;*.ogg)|*.wav;*.mp3;*.flac;*.ogg"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			for path in dialog.GetPaths():
				if path not in self.paths:
					self.paths.append(path)
		self.references.Set(self.paths)
		self.on_reference_selection()
		self.references.SetFocus()
		self.say(_("{count} reference recordings added.").format(count=len(self.paths)))

	def on_remove(self, event):
		self.stop_reference()
		for index in reversed(self.references.GetSelections()):
			del self.paths[index]
		self.references.Set(self.paths)
		self.on_reference_selection()
		self.references.SetFocus()

	def selected_language(self):
		return self.languages[self.language.GetSelection()][0]

	def on_reset_text(self, event):
		self.sample_text.ChangeValue(service.get_sample_text(self.selected_language()))
		self.sample_text.SetFocus()
		self.say(_("Default sample text restored."))

	def on_language(self, event):
		# Keep custom text when changing language; only replace an untouched default.
		if self.sample_text.GetValue() in [service.get_sample_text(key) for key, label in self.languages]:
			self.sample_text.ChangeValue(service.get_sample_text(self.selected_language()))

	def on_create(self, event):
		if self.playing:
			return
		if not self.paths:
			self.say(_("Add at least one reference recording."))
			self.add_button.SetFocus()
			return
		paths = list(self.paths)
		language = self.selected_language()
		options = self.reference_options()
		synthesis_settings = dict(GENERATION_PRESETS[self.generation_preset.GetSelection()][1])
		self.stop_reference()
		service.stop_preview()
		try:
			durations = run_busy(self, _("Checking how recordings contribute to this clone..."),
				lambda: [service.probe_audio_source(path)["durationSeconds"] for path in paths],
				button=self.create_button, completion_message="")
			coverage, unused = self.reference_coverage(paths, durations, options)
			if unused:
				answer = wx.MessageBox(coverage + _("\n\nContinue with this order and these settings? Choose No to change the recordings or conditioning lengths."),
					_("Some recordings may contribute no GPT style"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self)
				if answer != wx.YES:
					if not options["balance_style"]:
						self.balance.SetFocus()
						self.say(_("Cloning cancelled. Consider enabling Balance speech style, reordering recordings or longer conditioning."))
					else:
						self.references.SetFocus()
						self.say(_("Cloning cancelled. Recordings and settings are unchanged."))
					return
			record = run_busy(self, _("Cloning for testing. Loading the model may take tens of seconds..."),
				lambda: service.clone_voice_draft(paths, language, options, synthesis_settings),
				button=self.create_button, completion_message="")
		except Exception as error:
			self.say(_("Cloning failed: {error}").format(error=error))
			self.create_button.SetFocus()
			return
		previous = self.record
		self.record = record
		if previous:
			service.discard_voice_draft(previous)
		self.preview_button.Enable()
		self.save_button.Enable()
		self.preview_button.SetFocus()
		self.say(_("Clone ready for testing. Play a sample, or choose Save voice to keep it."))

	def on_save(self, event):
		if not self.record or self.playing:
			return
		from synthDrivers.maxlogic_xtts_v2._voice_store import DuplicateVoiceError
		with wx.TextEntryDialog(self, _("Name for the voice to save:"), _("Save cloned voice")) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				self.save_button.SetFocus()
				return
			name = dialog.GetValue().strip()
		if not name:
			self.say(_("The voice was not saved. Enter a name when you choose Save voice."))
			self.save_button.SetFocus()
			return
		# Language does not alter conditioning and may be changed while auditioning.
		self.record.metadata["language"] = self.selected_language()
		def save(overwrite=False):
			return run_busy(self, _("Saving voice..."),
				lambda: service.save_voice_draft(self.record, name, overwrite=overwrite),
				button=self.save_button, completion_message="")
		try:
			try:
				record = save()
			except DuplicateVoiceError:
				answer = wx.MessageBox(_("A voice named {name} already exists. Replace it with this clone? The existing voice files will be replaced.").format(name=name),
					_("Replace existing voice?"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self)
				if answer != wx.YES:
					self.save_button.SetFocus()
					self.say(_("Voice not saved. Your test clone is still available."))
					return
				record = save(overwrite=True)
		except Exception as error:
			self.save_button.SetFocus()
			self.say(_("Could not save voice: {error}. Your test clone is still available.").format(error=error))
			return
		self.on_change()
		try:
			run_busy(self, _("Refreshing available voices..."), lambda: service.refresh_active_synth(reason="voice-clone"),
				button=self.save_button, completion_message="")
		except Exception:
			self.say(_("Voice saved. Restart NVDA to refresh the active synthesizer's voice list."))
		else:
			self.say(_("Saved {name} to Installed voices.").format(name=record.display_name))
		self.save_button.SetFocus()

	def on_preview(self, event):
		if self.playing:
			service.stop_preview()
			self.say(_("Playback stopped. Finishing sample generation..."))
			return
		if not self.record:
			return
		text = self.sample_text.GetValue().strip()
		if not text:
			self.say(_("Enter sample text or reset it to the default."))
			self.sample_text.SetFocus()
			return
		self.stop_reference()
		self.playing = True
		self.reference_play_button.Disable()
		self.preview_button.SetLabel(_("Sto&p sample"))
		self._preview_busy = ButtonBusy(self.preview_button)
		self.create_button.Disable()
		self.save_button.Disable()
		self.say(_("Preparing sample..."))
		record = self.record
		def complete(status, error=None):
			if self._closed or not self or self.IsBeingDeleted():
				service.discard_voice_draft(record)
				return
			self.playing = False
			self.on_reference_selection()
			self._preview_busy.stop()
			self._preview_busy = None
			self.create_button.Enable()
			self.save_button.Enable()
			self.preview_button.SetLabel(_("&Play sample"))
			self.say(_("Sample failed: {error}").format(error=error) if error else (_("Sample finished.") if status == "completed" else _("Sample stopped.")))
		try:
			service.play_installed_voice_sample(record, on_complete=complete,
				preview_language=self.selected_language(), sample_text=text)
		except Exception as error:
			complete("error", str(error))

	def cleanup(self):
		self._closed = True
		self.stop_reference()
		if self._preview_busy:
			self._preview_busy.stop()
		# A cancelled synthesis can still be reading conditioning. Its completion
		# callback owns disposal until the worker has stopped using the draft.
		if self.record and not self.playing:
			service.discard_voice_draft(self.record)
		self.record = None
