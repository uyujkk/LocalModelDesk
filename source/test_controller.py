import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from controller import Controller, ControlError, CONTEXT, CONTEXTS, MODEL, QUANTS, Profile, command_line, loaded, matches, sync_config


class ControllerTests(unittest.TestCase):
    def setUp(self):
        workspace = Path(__file__).resolve().parent / "test-work"
        workspace.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace)
        self.root = Path(self.temp.name)
        self.agent = self.root / "agent"
        self.config = self.agent / "config" / "opencode" / "opencode.json"
        self.config.parent.mkdir(parents=True)
        self.original = {"permission": {"bash": "ask", "websearch": "allow", "task": "deny"},
                         "provider": {"strata": {"models": {MODEL: {"limit": {"context": 131072, "custom": 9}}}}},
                         "compaction": {"auto": True, "prune": False, "buffer": 60548}, "unrelated": [1, 2]}
        self.config.write_text(json.dumps(self.original), encoding="utf-8")
        (self.agent / "Start-Qwen-Desktop.cmd").write_text("@echo off\n", encoding="ascii")
        self.health = {"service": "strata", "model": MODEL, "max_context": CONTEXT, "loaded": True}

    def tearDown(self):
        self.temp.cleanup()

    def test_config_preserves_permissions_extra_limits_and_compaction(self):
        self.assertTrue(sync_config(self.config))
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(config["permission"], self.original["permission"])
        self.assertEqual(config["unrelated"], [1, 2])
        self.assertFalse(config["compaction"]["prune"])
        self.assertNotIn("buffer", config["compaction"])
        self.assertEqual(config["compaction"]["reserved"], 16384)
        self.assertEqual(config["provider"]["strata"]["models"][MODEL]["limit"],
                         {"context": CONTEXT, "input": 229376, "output": 32768, "custom": 9})
        backups = list(self.config.parent.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), self.original)
        self.assertFalse(sync_config(self.config))
        self.assertEqual(len(list(self.config.parent.glob("*.bak"))), 1)

    def test_bad_config_is_not_changed(self):
        self.config.write_text('{"provider":{}}', encoding="utf-8")
        before = self.config.read_bytes()
        with self.assertRaises(ControlError):
            sync_config(self.config)
        self.assertEqual(self.config.read_bytes(), before)

    def test_wrong_service_never_stopped_or_started(self):
        c = Controller(self.root)
        foreign = dict(self.health, model="different-model")
        with patch.object(c, "health", return_value=foreign), patch.object(c, "run_cli") as cli:
            with self.assertRaises(ControlError):
                c.stop()
            with self.assertRaises(ControlError):
                c.start()
            cli.assert_not_called()

    def test_loaded_service_reused(self):
        sync_config(self.config)
        c = Controller(self.root)
        with patch.object(c, "health", return_value=self.health), patch.object(c, "run_cli") as cli, \
                patch.object(c, "desktop_processes", return_value=[object()]):
            result = c.start()
            cli.assert_not_called()
            self.assertIn("已在运行", result["message"])

    def test_unloaded_service_is_stopped_before_start(self):
        c = Controller(self.root)
        states = [dict(self.health, loaded=False), dict(self.health, loaded=False), None, self.health]
        with patch.object(c, "health", side_effect=states), patch.object(c, "run_cli") as cli, \
                patch.object(c, "close", return_value={}), \
                patch.object(c, "desktop_processes", return_value=[object()]):
            c.start()
            self.assertEqual(len(cli.call_args_list), 2)
            self.assertIn("--stop", cli.call_args_list[0].args[0])
            self.assertNotIn("--stop", cli.call_args_list[1].args[0])

    def test_switch_iq3_256_uses_separate_preset_and_preserves_old_model(self):
        c = Controller(self.root)
        profile = Profile("IQ3_XXS", 262144)
        new_health = dict(self.health, model=profile.model)
        with patch.object(c, "health", side_effect=[self.health, self.health, None, new_health]), \
                patch.object(c, "run_cli") as cli, patch.object(c, "close", return_value={}) as close, \
                patch.object(c, "desktop_processes", side_effect=[[object()], [], [object()]]), \
                patch("controller.subprocess.Popen") as desktop:
            c.start(profile)
            close.assert_called_once_with(force=False)
            desktop.assert_called_once()
            plan, stop, launch = [call.args[0] for call in cli.call_args_list]
            self.assertEqual(plan[-1], "--plan")
            self.assertEqual(stop, ["launch", "--stop", "--preset", "qwen-dual12g-256k"])
            self.assertIn("qwen-iq3xxs-dual12g-256k", launch)
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(cfg["model"], f"strata/{profile.model}")
        models = cfg["provider"]["strata"]["models"]
        self.assertIn(MODEL, models)
        self.assertEqual(models[profile.model]["limit"]["input"], 229376)
        self.assertEqual(cfg["compaction"]["reserved"], 16384)
        self.assertEqual(cfg["permission"], self.original["permission"])

    def test_shorter_context_updates_open_code_budget(self):
        sync_config(self.config)
        profile = Profile("IQ3_XXS", 65536)
        sync_config(self.config, profile)
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        limit = cfg["provider"]["strata"]["models"][profile.model]["limit"]
        self.assertEqual((limit["context"], limit["input"], limit["output"]), (65536, 57344, 8192))
        self.assertEqual(cfg["compaction"]["reserved"], 4096)

    def test_picture_toggle_restarts_same_model_and_syncs_open_code(self):
        text = Profile("IQ3_XXS", 131072)
        picture = Profile("IQ3_XXS", 131072, True)
        c = Controller(self.root)
        health = dict(self.health, model=text.model, max_context=text.context, images=False)
        self.assertEqual(text.preset, picture.preset)
        for before, after in ((text, picture), (picture, text)):
            with self.subTest(images=after.images), \
                    patch.object(c, "health", side_effect=[dict(health, images=before.images),
                        dict(health, images=before.images), None, dict(health, images=after.images)]), \
                    patch.object(c, "run_cli") as cli, patch.object(c, "close", return_value={}), \
                    patch.object(c, "desktop_processes", return_value=[object()]):
                c.start(after)
                plan, stop, launch = [call.args[0] for call in cli.call_args_list]
                self.assertEqual(plan[-1], "--plan")
                self.assertEqual(stop[-1], text.preset)
                self.assertEqual(launch[launch.index("--vision") + 1], "cpu" if after.images else "off")
                model = json.loads(self.config.read_text(encoding="utf-8"))["provider"]["strata"]["models"][text.model]
                self.assertEqual(model["attachment"], after.images)
                self.assertEqual(model["modalities"]["input"], ["text", "image"] if after.images else ["text"])
                self.assertEqual(model["limit"]["context"], 131072)

    def test_strata_image_state_mismatch_does_not_change_open_code(self):
        picture = Profile("IQ3_XXS", 131072, True)
        c = Controller(self.root)
        wrong = dict(self.health, model=picture.model, max_context=picture.context, images=False)
        original = self.config.read_bytes()
        with patch.object(c, "health", side_effect=[None, wrong]), patch.object(c, "run_cli"), \
                patch.object(c, "desktop_processes", return_value=[]):
            with self.assertRaisesRegex(ControlError, "图片输入状态"):
                c.start(picture)
        self.assertEqual(self.config.read_bytes(), original)

    def test_command_quotes_paths_and_rejects_interpolation(self):
        command = command_line(Path(r"E:\模型 文件\strata.cmd"), ["launch", "--yes"])
        self.assertIn('"E:\\模型 文件\\strata.cmd" launch --yes', command)
        with self.assertRaises(ControlError):
            command_line(Path(r"E:\%TEMP%\strata.cmd"), ["launch"])
        with self.assertRaises(ControlError):
            command_line(Path(r"E:\strata.cmd"), ["launch & whoami"])
        with self.assertRaises(ControlError):
            command_line(Path(r"E:\strata.cmd"), ["launch", "--gpu-roles", "0=main,1=helper&whoami"])

    def test_strict_model_state(self):
        self.assertTrue(matches(self.health))
        self.assertTrue(loaded(self.health))
        self.assertFalse(loaded(dict(self.health, loaded="true")))
        self.assertFalse(matches(dict(self.health, max_context=True)))
        self.assertFalse(matches(dict(self.health, service="unrelated")))

    def test_actual_cli_wrapper_with_spaces_and_environment(self):
        root = self.root / "test install"
        cli = root / "terminal" / "0.1.57" / "bin" / "strata.cmd"
        cli.parent.mkdir(parents=True)
        cli.write_text("@echo off\necho QWENDESK_CLI_OK\necho %STRATA_SOURCE%\necho %*\nexit /b 0\n", encoding="ascii")
        output = []
        Controller(root, output.append).run_cli(["launch", "--preset", "qwen-dual12g-256k", "--yes"])
        self.assertIn("QWENDESK_CLI_OK", output)
        self.assertIn("modelscope", output)
        self.assertTrue(any("launch --preset qwen-dual12g-256k --yes" in line for line in output))
        for quant in QUANTS:
            for context in CONTEXTS:
                profile = Profile(quant, context)
                if profile.model == MODEL and context == CONTEXT:
                    continue
                with self.subTest(quant=quant, context=context):
                    output.clear()
                    Controller(root, output.append).plan(profile)
                    command = next(line for line in output if line.startswith("launch "))
                    self.assertIn('--gpu-roles "0=main,1=helper"', command)
                    self.assertIn(f'--data-dir "{root / "models"}"', command)
                    self.assertTrue(command.endswith("--plan"))

    def test_close_with_no_process_does_not_kill_and_can_stop_after(self):
        c = Controller(self.root)
        with patch.object(c, "desktop_processes", return_value=[]), patch.object(c, "stop", return_value={"message": "stopped"}) as stop:
            self.assertIn("已关闭", c.close()["message"])
            stop.assert_not_called()
            self.assertEqual(c.close(stop_after=True), {"message": "stopped"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
