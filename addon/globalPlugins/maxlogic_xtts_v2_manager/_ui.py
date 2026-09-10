"""Native progress and background loading without blocking NVDA's event loop."""
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


def run_busy(parent, message, work):
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
	return result[0]
