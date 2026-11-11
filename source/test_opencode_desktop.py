import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from controller import CONTEXT, MODEL, Controller, Profile
from opencode_desktop import BackgroundService, Desktop, OpenCodeV2, find_desktop


class OpenCodeDesktopTests(unittest.TestCase):
    def setUp(self):
        workspace = Path(__file__).resolve().parent / "test-work"
        workspace.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = self.root / "agent"
        self.installed = self.root / "custom install" / "OpenCode.exe"
        self.old = self.agent / "desktop" / "OpenCode.exe"
        for exe in (self.installed, self.old):
            (exe.parent / "resources").mkdir(parents=True)
            (exe.parent / "resources" / "app.asar").touch()
            exe.touch()
        self.desktop = Desktop(self.installed, (2, 2, 7))
        self.desktop.cli.touch()
        self.adapter = OpenCodeV2(self.desktop, self.agent, "http://127.0.0.1:8080")
        self.legacy_config = self.agent / "config" / "opencode" / "opencode.json"
        self.legacy_config.parent.mkdir(parents=True)
        self.legacy_config.write_text('{"permission":{"shell":"ask"}}', encoding="utf-8")
        self.service = BackgroundService("test-service", 101, "http://127.0.0.1:9001", "fixture")

    def test_discovers_updated_desktop_and_supports_explicit_paths(self):
        versions = {self.installed: (2, 2, 7), self.old: (1, 19, 3)}
        with patch("opencode_desktop.installed_desktops", return_value=[self.installed]), \
                patch("opencode_desktop.file_version", side_effect=versions.__getitem__):
            self.assertEqual(find_desktop(self.agent), self.desktop)
            self.assertEqual(find_desktop(self.agent, self.old).version, (1, 19, 3))
            self.assertIsNone(find_desktop(self.agent, self.root / "missing.exe"))

    def test_native_config_switches_context_and_images_and_installs_plugin_once(self):
        original = self.legacy_config.read_bytes()
        picture = Profile("IQ3_XXS", 65536, True)
        self.assertTrue(self.adapter.sync(picture, apply=False))
        self.assertFalse(self.adapter.profile.exists())
        self.assertTrue(self.adapter.sync(picture))
        self.assertFalse(self.adapter.sync(picture))
        data = json.loads(self.adapter.config.read_bytes())
        self.assertEqual(data["model"], f"strata/{picture.model}")
        self.assertEqual(data["permission"], {"shell": "ask"})
        self.assertEqual(data["compaction"]["buffer"], 4096)
        model = data["providers"]["strata"]["models"][picture.model]
        self.assertEqual(model["limit"], {"context": 65536, "input": 57344, "output": 8192})
        self.assertEqual(model["capabilities"]["input"], ["text", "image"])
        self.assertEqual(data["plugins"], [{"package": str(self.adapter.plugin)}])
        self.assertTrue(Path(data["instructions"][0]).is_file())
        self.assertTrue(self.adapter.sync(Profile("IQ3_XXS", 32768)))
        data = json.loads(self.adapter.config.read_bytes())
        self.assertEqual(data["providers"]["strata"]["models"][picture.model]["capabilities"]["input"], ["text"])
        self.assertEqual(len(data["plugins"]), 1)
        self.assertEqual(len(list(self.adapter.config.parent.glob("*.bak"))), 1)
        self.assertEqual(self.legacy_config.read_bytes(), original)

    def test_external_backend_requires_restart_before_touching_strata(self):
        health = {"service": "strata", "model": MODEL, "max_context": CONTEXT, "loaded": True}
        with patch("controller.find_desktop", return_value=self.desktop), \
                patch("controller.background_service", return_value=self.service):
            controller = Controller(self.root)
            with patch.object(controller, "health", return_value=health) as check_health, \
                    patch.object(controller, "desktop_processes", return_value=[]), \
                    patch.object(OpenCodeV2, "stop_service") as stop, \
                    patch.object(OpenCodeV2, "launch") as launch, \
                    patch.object(controller, "wait_v2", return_value={"message": "ready"}):
                self.assertEqual(controller.start(), {"needs_service_restart": True})
                check_health.assert_not_called()
                stop.assert_not_called()
                self.assertEqual(controller.start(restart_service=True), {"message": "ready"})
                stop.assert_called_once_with(self.service)
                launch.assert_called_once()

    def test_ready_checks_both_model_and_gui_bundle(self):
        profile = Profile("IQ2_XS", CONTEXT)
        self.adapter.sync(profile)
        models = {"data": [{"providerID": "strata", "id": MODEL, "limit": {"context": CONTEXT},
                            "capabilities": {"input": ["text"]}}]}
        bundle = (self.adapter.plugin / "gui-experimental.cjs").read_bytes().decode("utf-8")
        with patch.object(BackgroundService, "get", side_effect=[models, {"data": {"code": bundle}}]):
            self.assertTrue(self.adapter.ready(self.service, profile))
        with patch.object(BackgroundService, "get", side_effect=[models, {"data": {"code": "different"}}]):
            self.assertFalse(self.adapter.ready(self.service, profile))


if __name__ == "__main__":
    unittest.main()
