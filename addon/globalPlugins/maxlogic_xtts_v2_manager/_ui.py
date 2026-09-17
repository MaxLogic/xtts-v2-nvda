"""Native progress and background loading without blocking NVDA's event loop."""
import math
import threading

import gui
import ui
import wx


def show_modal(dialog):
	if not hasattr(gui, "displayDialogAsModal"):
		gui.runScriptModalDialog(dialog)
		return

	def run():
		try:
			gui.displayDialogAsModal(dialog)
		finally:
			dialog.Destroy()

	wx.CallAfter(run)


class NamedAccessible(wx.Accessible):
	def GetName(self, childId):
		if childId == 0:
			return wx.ACC_OK, self.GetWindow().GetName()
		return super().GetName(childId)


class StatusText(wx.TextCtrl):
	"""Persistent, keyboard-readable feedback with native text wrapping."""
	def __init__(self, parent, label="", name=None):
		super().__init__(parent, value=label, name=name or _("Status"),
			style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 55))
		self.SetAccessible(NamedAccessible(self))

	def SetLabel(self, label):
		self.ChangeValue(label)

	def GetLabel(self):
		return self.GetValue()


def report_loading(owner, is_loading, message=None):
	owner._loading_message = (message or _("Loading...")) if is_loading else None
	dialog = wx.GetTopLevelParent(owner)
	if hasattr(dialog, "refresh_loading_feedback"):
		wx.CallAfter(dialog.refresh_loading_feedback)


def remember_focus(owner):
	"""Call before disabling a page's controls for a reload.

	Windows leaves the focus on a control that becomes disabled, and the
	keyboard then reaches nothing until the user clicks or switches windows.
	"""
	if getattr(owner, "_focus_before_loading", None) is not None:
		return
	focused = wx.Window.FindFocus()
	if focused is not None and owner.IsDescendant(focused):
		owner._focus_before_loading = focused


def restore_focus(owner, fallback):
	"""Call after re-enabling the controls. Does nothing if the user has moved on."""
	target = getattr(owner, "_focus_before_loading", None)
	owner._focus_before_loading = None
	if target is None or not owner.IsShownOnScreen() or not wx.GetTopLevelParent(owner).IsActive():
		return
	current = wx.Window.FindFocus()
	if current is not None and current is not target and current.IsEnabled():
		return
	if not target or target.IsBeingDeleted() or not target.IsEnabled():
		target = fallback
	if target and target.IsEnabled():
		target.SetFocus()


class DeferredPanel(wx.Panel):
	"""Construct an optional page only after the user selects it."""
	def __init__(self, parent, factory):
		super().__init__(parent)
		self.factory = factory
		self.content = None
		self.pending = False
		self.hint = wx.StaticText(self, label=_("Loading page..."))
		self.retry = wx.Button(self, label=_("&Retry loading page"))
		self.retry.Hide()
		self.retry.Bind(wx.EVT_BUTTON, lambda event: self.load())
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.hint, 0, wx.ALL, 10)
		sizer.Add(self.retry, 0, wx.ALL, 10)
		self.SetSizer(sizer)

	def load(self):
		if self.content is not None or self.pending:
			return
		self.pending = True
		parent = self.GetParent()
		index = parent.FindPage(self) if isinstance(parent, wx.Notebook) else wx.NOT_FOUND
		name = parent.GetPageText(index) if index != wx.NOT_FOUND else _("page")
		report_loading(self, True, _("Loading {name}...").format(name=name))
		# Allow the loading field to paint and NVDA to announce it before construction.
		wx.CallLater(50, self._build)

	def _build(self):
		if not self or self.IsBeingDeleted():
			return
		try:
			self.content = self.factory(self)
		except Exception as error:
			for child in self.GetChildren():
				if child not in (self.hint, self.retry):
					child.Destroy()
			self.hint.SetLabel(_("This page could not be opened. {error}").format(error=error))
			self.retry.Show()
			self.pending = False
			report_loading(self, False)
			self.Layout()
			if self.IsShownOnScreen():
				ui.message(self.hint.GetLabel())
			return
		report_loading(self, False)
		self.hint.Hide()
		self.retry.Hide()
		self.GetSizer().Add(self.content, 1, wx.EXPAND)
		self.Layout()
		dialog = wx.GetTopLevelParent(self)
		if hasattr(dialog, "fit_active_page"):
			wx.CallAfter(dialog.fit_active_page)


def load_async(owner, work, complete, failed):
	owner._load_generation = getattr(owner, "_load_generation", 0) + 1
	generation = owner._load_generation
	owner.Disable()

	def finish(result, error):
		if not owner or owner.IsBeingDeleted() or generation != owner._load_generation:
			return
		owner.Enable()
		if error is not None:
			failed(error)
		else:
			complete(result)

	def worker():
		try:
			result = work()
		except Exception as error:
			wx.CallAfter(finish, None, str(error))
		else:
			wx.CallAfter(finish, result, None)

	threading.Thread(target=worker, name="MaxLogicManagerLoad", daemon=True).start()


class ButtonBusy:
	"""Animate a native button's bitmap without changing its spoken label."""
	def __init__(self, button):
		self.button = button
		self.original = button.GetBitmap()
		self.original_disabled = button.GetBitmapDisabled()
		self.frames = self._make_frames(button)
		self.frame = 0
		self.timer = wx.Timer(button)
		button.Bind(wx.EVT_TIMER, self._tick, self.timer)
		button.Bind(wx.EVT_WINDOW_DESTROY, self._destroyed)
		self._tick(None)
		button.GetParent().Layout()
		self.timer.Start(90)

	@staticmethod
	def _make_frames(button):
		size = button.FromDIP(16)
		colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNTEXT)
		frames = []
		for frame in range(8):
			bitmap = wx.Bitmap(size, size, 32)
			dc = wx.MemoryDC(bitmap)
			dc.SetBackground(wx.Brush(button.GetBackgroundColour()))
			dc.Clear()
			dc.SetPen(wx.TRANSPARENT_PEN)
			for dot in range(8):
				alpha = 55 + ((dot - frame) % 8) * 28
				dc.SetBrush(wx.Brush(wx.Colour(colour.Red(), colour.Green(), colour.Blue(), alpha)))
				angle = dot * math.pi / 4
				dc.DrawCircle(round(size / 2 + math.cos(angle) * size * .32), round(size / 2 + math.sin(angle) * size * .32), max(1, size // 10) if dot != frame else max(2, size // 7))
			dc.SelectObject(wx.NullBitmap)
			frames.append(bitmap)
		return frames

	def _tick(self, event):
		if not self.button or self.button.IsBeingDeleted():
			self.stop()
			return
		bitmap = self.frames[self.frame]
		self.button.SetBitmap(bitmap)
		self.button.SetBitmapDisabled(bitmap)
		self.frame = (self.frame + 1) % len(self.frames)

	def _destroyed(self, event):
		if event.GetEventObject() is self.button:
			self.stop()
		event.Skip()

	def stop(self):
		if self.button is None:
			return
		self.timer.Stop()
		button, self.button = self.button, None
		if button and not button.IsBeingDeleted():
			button.Unbind(wx.EVT_TIMER, handler=self._tick, source=self.timer)
			button.Unbind(wx.EVT_WINDOW_DESTROY, handler=self._destroyed)
			button.SetBitmap(self.original)
			button.SetBitmapDisabled(self.original_disabled)
			button.GetParent().Layout()


def run_busy(parent, message, work, *, button=None, completion_message=None):
	"""Run I/O while the visible manager paints, with input locked until return."""
	if button is None:
		focused = wx.Window.FindFocus()
		if isinstance(focused, wx.Button) and (focused.GetParent() is parent or parent.IsDescendant(focused)):
			button = focused
	if button is None:
		return _run_busy_dialog(parent, message, work, completion_message)
	top = wx.GetTopLevelParent(parent)
	if getattr(top, "_operation_busy", False):
		raise RuntimeError(_("An operation is already running."))
	indicator = ButtonBusy(button)
	loop = wx.GUIEventLoop()
	result, errors = [], []
	was_enabled = top.IsEnabled()
	focus = wx.Window.FindFocus()
	top._operation_busy = True
	top.Disable()
	ui.message(message)

	def worker():
		try:
			result.append(work())
		except Exception as error:
			errors.append(error)
		finally:
			wx.CallAfter(loop.Exit, 0)

	try:
		# Defer starting until the nested loop exists, including instant failures.
		wx.CallAfter(lambda: threading.Thread(target=worker, name="MaxLogicManagerOperation", daemon=True).start())
		loop.Run()
	finally:
		indicator.stop()
		if top and not top.IsBeingDeleted():
			top._operation_busy = False
			top.Enable(was_enabled)
			if focus and not focus.IsBeingDeleted() and focus.IsEnabled() and top.IsActive():
				focus.SetFocus()
	if errors:
		raise errors[0]
	if completion_message:
		ui.message(completion_message)
	return result[0]


def _run_busy_dialog(parent, message, work, completion_message=None):
	"""Keep a modal operation and its errors on the UI thread; do I/O in a worker."""
	dialog = wx.Dialog(parent, title=message, style=wx.DEFAULT_DIALOG_STYLE)
	sizer = wx.BoxSizer(wx.VERTICAL)
	status = wx.TextCtrl(
		dialog, value=message + "\n" + _("Please wait. This window closes when the operation finishes."),
		style=wx.TE_MULTILINE | wx.TE_READONLY, size=(460, 100), name=_("Operation status"),
	)
	status.SetAccessible(NamedAccessible(status))
	sizer.Add(status, 1, wx.EXPAND | wx.ALL, 12)
	dialog.SetSizerAndFit(sizer)
	dialog.CentreOnParent()
	result = []
	errors = []

	def prevent_close(event):
		if event.CanVeto():
			event.Veto()
		ui.message(_("The operation is still running. Please wait."))

	dialog.Bind(wx.EVT_CLOSE, prevent_close)
	dialog.Bind(wx.EVT_BUTTON, lambda event: ui.message(_("The operation is still running. Please wait.")), id=wx.ID_CANCEL)

	def worker():
		try:
			result.append(work())
		except Exception as error:
			errors.append(error)
		finally:
			wx.CallAfter(dialog.EndModal, wx.ID_OK)

	# Start only after ShowModal has established its event loop.
	wx.CallAfter(lambda: threading.Thread(target=worker, name="MaxLogicManagerOperation", daemon=True).start())
	try:
		status.SetFocus()
		dialog.ShowModal()
	finally:
		dialog.Destroy()
	if errors:
		raise errors[0]
	if completion_message:
		ui.message(completion_message)
	return result[0]
