# coding: utf-8

import addonHandler
import globalPluginHandler
import gui
import wx

from . import service
from .voice_manager import MaxLogicVoiceManagerDialog
from ._ui import show_modal


addonHandler.initTranslation()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self, *args, **kwargs):
		super(GlobalPlugin, self).__init__(*args, **kwargs)
		self._menu_item = gui.mainFrame.sysTrayIcon.menu.Insert(
			4,
			wx.ID_ANY,
			_("MaxLogic XTTS v2 &voice manager..."),
			_("Open the voice manager to install, remove, preview, or manage MaxLogic XTTS v2 voice profiles"),
		)
		gui.mainFrame.sysTrayIcon.menu.Bind(wx.EVT_MENU, self.on_open_manager, self._menu_item)

	def on_open_manager(self, event):
		dialog = MaxLogicVoiceManagerDialog()
		show_modal(dialog)

	def terminate(self):
		try:
			service.stop_preview()
			service.close_preview_helper()
			service.close_preview_player()
			gui.mainFrame.sysTrayIcon.menu.DestroyItem(self._menu_item)
		except Exception:
			pass
