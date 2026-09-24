"""Manager transitions with native-control contracts and no NVDA installation.

wx is unavailable in the standalone test Python. The list fixture mirrors
SetItems clearing selection; service callbacks are retained until explicitly
delivered, so late preview events exercise the real request ownership checks.
"""
import builtins
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Control:
    def __init__(self):
        self.enabled = True
        self.selection = -1
        self.label = ""

    def Enable(self, enabled=True): self.enabled = enabled
    def Disable(self): self.enabled = False
    def IsEnabled(self): return self.enabled
    def SetLabel(self, label): self.label = label
    def GetLabel(self): return self.label
    def GetSelection(self): return self.selection
    def SetSelection(self, index): self.selection = index
    def SetItems(self, items): self.selection = -1
    def Show(self, shown=True): pass
    def Layout(self): pass
    def IsBeingDeleted(self): return False
    def IsShownOnScreen(self): return False


class ManagerWorkflowTests(unittest.TestCase):
    def setUp(self):
        package = "manager_workflows"
        root = Path(__file__).resolve().parents[1] / "addon/globalPlugins/maxlogic_xtts_v2_manager"
        self.service = types.SimpleNamespace(
            stop_preview=Mock(), get_setup_status=lambda: {"message": "Ready"},
            play_installed_voice_sample=Mock(), play_catalog_voice_sample=Mock(),
        )
        wx = types.SimpleNamespace(Panel=Control, Dialog=Control, NOT_FOUND=-1,
            YES=1, YES_NO=2, NO_DEFAULT=4, ICON_QUESTION=8, OK=16, ICON_ERROR=32)
        self.gui = types.SimpleNamespace(messageBox=Mock(return_value=wx.YES))
        ui = types.SimpleNamespace(**{name: Mock() for name in (
            "ButtonBusy", "DeferredPanel", "StatusText", "load_async", "remember_focus",
            "report_loading", "restore_focus", "run_busy")})
        modules = {
            package: types.ModuleType(package), package + ".service": self.service,
            package + ".clone_voice": types.SimpleNamespace(CloneVoicePanel=Control),
            package + ".loading_sounds_page": types.SimpleNamespace(LoadingSoundsPanel=Control),
            package + "._ui": ui, "wx": wx, "wx.lib": types.ModuleType("wx.lib"),
            "wx.lib.scrolledpanel": types.SimpleNamespace(ScrolledPanel=Control),
            "gui": self.gui, "gui.nvdaControls": types.SimpleNamespace(CustomCheckListBox=Control),
            "ui": types.SimpleNamespace(message=Mock()), "logHandler": types.SimpleNamespace(log=Mock()),
        }
        for context in (patch.dict(sys.modules, modules), patch.object(builtins, "_", lambda s: s, create=True)):
            context.start()
            self.addCleanup(context.stop)
        spec = importlib.util.spec_from_file_location(package + ".voice_manager", root / "voice_manager.py")
        self.manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.manager)

    def panel(self, name):
        cls = getattr(self.manager, name)
        panel = cls.__new__(cls)
        for key in ("voice_list", "builtin_list", "preview_button", "preview_language_choice",
                    "folder_button", "current_button", "remove_button", "install_button", "refresh_button",
                    "setup_button", "search_button", "search_text", "gender_choice", "language_choice",
                    "hide_installed_checkbox", "select_button", "clear_button", "download_button",
                    "setup_status", "result_hint", "empty_user_hint"):
            setattr(panel, key, Control())
        panel.switcher_button = None
        panel._user_voices = [types.SimpleNamespace(voice_id="a", source="user", display_name="Alice", metadata={})]
        panel._builtin_voices = []
        panel._entries = panel._visible_entries = [{"id": "a", "displayName": "Alice"}]
        panel._checked_ids = set()
        panel._preview_in_progress = panel._preview_playing = False
        panel._preview_request_id = 0
        panel._preview_source_list = None
        panel._setup_busy = None
        panel._allow_refresh = True
        panel._preview_language_options = [("", "Auto")]
        panel.voice_list.SetSelection(0)
        return panel

    def test_preparing_sample_can_be_stopped_and_late_callbacks_are_ignored(self):
        for name in ("InstalledVoicesPanel", "CatalogVoicesPanel", "HuggingFaceSearchPanel"):
            with self.subTest(panel=name):
                panel = self.panel(name)
                panel.on_play_sample(None)
                player = self.service.play_installed_voice_sample if name == "InstalledVoicesPanel" else self.service.play_catalog_voice_sample
                callbacks = player.call_args.kwargs
                self.assertTrue(panel.preview_button.IsEnabled(), "Stop must remain keyboard reachable while preparing")
                self.assertIn("Stop", panel.preview_button.GetLabel().replace("&", ""))
                panel.on_play_sample(None)
                self.assertFalse(panel._preview_in_progress)
                self.assertTrue(panel.preview_language_choice.IsEnabled())
                callbacks["on_playback_started"]()
                callbacks["on_complete"]("completed", None)
                self.assertFalse(panel._preview_playing, "late callbacks revived a stopped preview")

    def test_refresh_preserves_selected_voice_identity_after_reordering(self):
        for source in ("user", "builtin"):
            with self.subTest(source=source):
                panel = self.panel("InstalledVoicesPanel")
                selected = panel._user_voices[0]
                if source == "builtin":
                    selected.source = "package"
                    panel._user_voices, panel._builtin_voices = [], [selected]
                    panel.voice_list.SetSelection(-1)
                    panel.builtin_list.SetSelection(0)
                other = types.SimpleNamespace(voice_id="b", source=selected.source, display_name="Bob", metadata={})
                inventory = {"user": [], "builtin": []}
                inventory[source] = [other, selected]
                panel._apply_inventory(inventory, {"message": "Ready"})
                self.assertEqual(panel._selected_record().voice_id if panel._selected_record() else None, "a")

    def test_failed_preset_replacement_reports_error_instead_of_escaping_event_handler(self):
        class DuplicateError(Exception):
            pass
        self.service.VoiceSwitcherDuplicateError = DuplicateError
        self.service.add_voice_to_switcher = Mock(side_effect=[DuplicateError(), OSError("Disk is full")])
        panel = self.panel("InstalledVoicesPanel")
        panel.on_add_to_switcher(None)
        self.assertIn("Disk is full", self.gui.messageBox.call_args.args[0])

    def test_failed_file_replacement_reports_error_instead_of_escaping_event_handler(self):
        class DuplicateError(Exception):
            pass
        self.service.DuplicateVoiceError = DuplicateError
        self.service.install_local_voice = Mock(side_effect=[DuplicateError(), OSError("Disk is full")])
        self.gui.mainFrame = types.SimpleNamespace(prePopup=Mock(), postPopup=Mock())
        wx = self.manager.wx
        wx.ID_OK, wx.FD_OPEN, wx.FD_FILE_MUST_EXIST, wx.ICON_WARNING = 1, 2, 4, 8
        wx.FileDialog = Mock(return_value=types.SimpleNamespace(ShowModal=lambda: wx.ID_OK, GetPath=lambda: "voice.zip"))
        panel = self.panel("InstalledVoicesPanel")
        panel._run_busy = lambda message, work, **kwargs: work()
        panel.on_install(None)
        self.assertIn("Disk is full", self.gui.messageBox.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
