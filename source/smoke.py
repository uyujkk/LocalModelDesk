"""Local rendering and background worker acceptance; no real Strata calls."""
from pathlib import Path
import json
import time

from PySide6.QtCore import QThread, QRect

from app import Window, Task
from controller import MODEL, CONTEXT


def run(app, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    window = Window(output / "settings-fixture", test=True)
    window.theme.setCurrentIndex(window.theme.findData("light"))
    window.show()
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-light.png"))
    assert window.model_badge.text() == "未连接"
    window.set_busy(True)
    assert not window.root.isEnabled() and not window.start_button.isEnabled()
    window.set_busy(False)
    assert window.root.isEnabled()
    health = {"service": "strata", "model": MODEL, "max_context": CONTEXT, "loaded": True}
    window.render_status({"health": health, "desktop_count": 1})
    assert "已就绪" in window.model_badge.text()
    assert "运行中" in window.desktop_status.text()
    window.quant.setCurrentText("IQ3_XXS")
    assert window.selected_profile().context == 65536
    assert "IQ2_XS / 256K" in window.active_model.text()
    window.context_pick.setCurrentIndex(window.context_pick.findData(262144))
    assert window.selected_profile().quant == "IQ3_XXS"
    assert "208K" in window.threshold_value.text()
    assert "内存上限" in window.model_hint.text()
    assert "切换" in window.start_button.text()
    window.images_pick.setChecked(True)
    assert window.selected_profile().images
    assert "图片已关闭" in window.active_model.text()
    selected = window.selected_profile()
    window.render_status({"health": {"service": "strata", "model": selected.model,
        "max_context": selected.context, "loaded": True, "images": True}, "desktop_count": 0})
    assert "图片已开启" in window.active_model.text()
    assert not "切换" in window.start_button.text()
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-model-switch.png"))
    window.theme.setCurrentIndex(window.theme.findData("dark"))
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-dark.png"))
    window.theme.setCurrentIndex(window.theme.findData("light"))
    window.resize(980, 720)
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-small.png"))
    for item in [window.start_button, window.close_button, window.unload_button, window.all_button]:
        assert item.width() >= item.fontMetrics().horizontalAdvance(item.text()) + 16 and item.height() >= 38
    window.navigate(3)
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-settings.png"))
    window.navigate(2)
    window.log("测试日志：中文输出和操作信息正常显示。")
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-logs.png"))
    from monitor_fixture import sample
    from monitor_ui import overview_model
    window.resize(1250, 900)
    window.navigate(1)
    fixture = sample()
    fixture["live"].update(state="reading", prompt_read=50471, prompt_total=56834, prompt_tokens=56834,
                           generated=0, prefill_tok_s_mean=802.7)
    fixture["hardware"].update(gpu_pcie_gen=2, gpu_pcie_width=16)
    fixture["hardware_static"] = {"cores": 32, "threads": 64}
    fixture["history"] = {"tok_s": [12, 24, 120, 42, 70, 160, 128],
                          "gpu_util": [40, 65, 55, 91, 80, 66],
                          "gpu_temp": [60, 61, 63, 65], "cpu_pct": [4, 14, 9, 13],
                          "gpu_pcie_rx_mb": [0, 50, 0, 180, 0, 190, 60],
                          "disk_read_mb": [0, 0, 12.1, 0, 1.2], "gpu_power": [62, 95, 130, 122, 160]}
    window.monitor.received(overview_model(fixture, "metrics"))
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-embedded-overview.png"))
    canvas = window.monitor.canvas
    def frame_text():
        return "\n".join("".join(segment.text for segment in row) for row in canvas.lines)
    frame = frame_text()
    assert "802.7" in frame and "89%" in frame
    assert "RTX 3080 Ti" in frame and "Recent requests" in frame and "System RAM" in frame
    window.resize(980, 720)
    app.processEvents()
    window.grab().save(str(output / "QwenDesk-embedded-compact.png"))
    assert "GPU temp" in frame_text()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(canvas, Qt.Key.Key_Space)
    assert window.monitor.paused
    QTest.keyClick(canvas, Qt.Key.Key_R)
    assert not window.monitor.paused
    window.monitor.failed("offline test")
    assert "offline test" in frame_text() and "802.7" not in frame_text()
    received = []
    task = Task(lambda emit: {"thread_test": True})
    task.signals.result.connect(lambda value: received.append((value, QThread.currentThread() == app.thread())))
    window.pool.start(task)
    deadline = time.monotonic() + 5
    while not received and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert received and received[0][1], "Worker callback did not run on UI thread"
    from unittest.mock import patch
    from PySide6.QtWidgets import QLineEdit, QPushButton
    from gpu_power import parse_gpus
    import gpu_power

    first = "GPU-12345678-abcd-1234-5678-123456789abc"
    second = "GPU-87654321-dcba-4321-8765-cba987654321"
    fixture = (
        f"0, RTX 3080 Ti, {first}, 151.25, 250.00, 350.00, 100.00, 400.00\n"
        f"1, RTX 3080, {second}, 90.00, 250.00, 350.00, 120.00, 380.00\n"
    )
    initial = parse_gpus(fixture)
    window.navigate(window.power_page_index)
    window.render_power(initial)
    app.processEvents()
    window.grab().save(str(output / "LocalModelDesk-power-zh.png"))
    second_card = window.power_cards.itemAt(1).widget()
    editor = second_card.findChild(QLineEdit)
    editor.setText("100")
    buttons = {button.text(): button for button in second_card.findChildren(QPushButton)}
    buttons["应用"].click()
    assert "120.00" in window.power_status.text()
    editor.setText("200")
    after_apply = parse_gpus(fixture.replace("90.00, 250.00", "90.00, 200.00"))
    after_reset = parse_gpus(fixture.replace("90.00, 250.00", "90.00, 350.00"))
    with patch.object(window, "confirm", return_value=True), \
         patch.object(gpu_power, "query_gpus", side_effect=[initial, after_apply, after_apply, after_reset]), \
         patch.object(gpu_power, "run_elevated") as elevate:
        buttons["应用"].click()
        deadline = time.monotonic() + 5
        while window.power_busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert "200" in window.power_status.text()
        second_card = window.power_cards.itemAt(1).widget()
        next(button for button in second_card.findChildren(QPushButton) if button.text() == "恢复默认").click()
        deadline = time.monotonic() + 5
        while window.power_busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert "350" in window.power_status.text()
        assert [call.args[1] for call in elevate.call_args_list] == [
            ["-i", second, "-pl", "200"], ["-i", second, "-pl", "350.00"],
        ]
    assert not parse_gpus(f"0, RTX 3080, {second}, [N/A], [N/A], [N/A], [N/A], [N/A]")[0].adjustable

    english_state = output / "settings-english"
    english_state.mkdir(exist_ok=True)
    (english_state / "settings.json").write_text(json.dumps({"language": "en"}), encoding="utf-8")
    english = Window(english_state, test=True)
    english.show()
    assert english.windowTitle() == "Local Model Desk"
    assert english.start_button.text() == "Start model and open OpenCode"
    english.navigate(english.power_page_index)
    english.render_power(initial)
    assert english.power_status.text() == "NVIDIA GPUs detected: 2"
    assert english.power_refresh.text() == "Refresh readings"
    app.processEvents()
    english.grab().save(str(output / "LocalModelDesk-power-en.png"))
    english.navigate(3)
    app.processEvents()
    english.grab().save(str(output / "LocalModelDesk-settings-en.png"))
    english.close()
    window.pool.waitForDone(5000)
    window.close()
    app.processEvents()
    (output / "checks.json").write_text(json.dumps({"ui_load": True, "light_dark": True,
        "small_layout": True, "busy_controls": True, "status_render": True,
        "background_callback_on_ui_thread": True, "gpu_power": True, "english_ui": True}, indent=2), encoding="utf-8")
    return 0
