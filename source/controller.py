"""QwenDesk engine control. No Qt dependency; subprocesses are scoped to this install."""
from __future__ import annotations

from i18n import tr

import ctypes
from copy import deepcopy
from dataclasses import dataclass
import json
import locale
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable

import psutil

MODEL = "qwen3.8-flash-next-iq2_xs"
PRESET = "qwen-dual12g-256k"
CONTEXT = 262144
DUAL_GPU_ROLES = "0=main,1=helper"

@dataclass(frozen=True)
class Profile:
    quant: str
    context: int

    @property
    def model(self):
        return f"qwen3.8-flash-next-{self.quant.lower()}"

    @property
    def context_name(self):
        return f"{self.context // 1024}K"

    @property
    def preset(self):
        if self.quant == "IQ2_XS" and self.context == CONTEXT:
            return PRESET
        quant = self.quant.lower().replace("_", "")
        return f"qwen-{quant}-dual12g-{self.context_name.lower()}"

    @property
    def output(self):
        return self.context // 8

    @property
    def input(self):
        return self.context - self.output

    @property
    def reserved(self):
        return self.context // 16

    def launch_args(self, data_dir: Path):
        if self == DEFAULT_PROFILE:
            return ["launch", "--preset", self.preset]
        return ["launch", "--family", "qwen", "--model", self.quant.lower(),
                "--context", self.context_name.lower(), "--gpu-roles", DUAL_GPU_ROLES,
                "--split-engine", "off", "--parallel", "1", "--kv", "int8",
                "--vision", "off", "--drafting", "off", "--tune", "skip", "--access", "local",
                "--port", "8080", "--data-dir", str(data_dir), "--save-preset", self.preset]


QUANTS = ("IQ2_XS", "IQ3_XXS")
CONTEXTS = (32768, 65536, 131072, 262144)
DEFAULT_PROFILE = Profile("IQ2_XS", CONTEXT)


class ControlError(RuntimeError):
    pass


def matches(health: dict | None, profile=DEFAULT_PROFILE) -> bool:
    return bool(health and health.get("service") == "strata"
                and health.get("model") == profile.model
                and type(health.get("max_context")) is int
                and health["max_context"] == profile.context)


def running_profile(health):
    if not health:
        return None
    for quant in QUANTS:
        for context in CONTEXTS:
            profile = Profile(quant, context)
            if matches(health, profile):
                return profile
    return None


def loaded(health: dict | None) -> bool:
    return bool(health and health.get("loaded") is True)


def sync_config(path: Path, profile=DEFAULT_PROFILE, *, apply=True) -> bool:
    """Update only model selection/limits; preserve unrelated config and permissions."""
    original = path.read_text(encoding="utf-8-sig")
    config = json.loads(original)
    try:
        models = config["provider"]["strata"]["models"]
        template = models.get(profile.model) or models.get(MODEL)
        if not isinstance(template, dict):
            raise KeyError(profile.model)
    except (KeyError, TypeError) as exc:
        raise ControlError(tr('OpenCode 没有配置当前 Qwen 模型，请检查安装位置。')) from exc
    before = json.dumps(config, sort_keys=True, ensure_ascii=False)
    selected = f"strata/{profile.model}"
    previous_id = str(config.get("model", "")).removeprefix("strata/")
    previous_context = models.get(previous_id, {}).get("limit", {}).get("context")
    profile_changed = config.get("model") != selected or previous_context != profile.context
    if profile.model not in models:
        models[profile.model] = deepcopy(template)
        models[profile.model]["name"] = f"Qwen3.8-Flash-Next {profile.quant} via Strata"
    models[profile.model].setdefault("limit", {}).update(
        context=profile.context, input=profile.input, output=profile.output)
    config.update(model=selected, small_model=selected)
    compaction = config.setdefault("compaction", {})
    compaction.pop("buffer", None)
    compaction.setdefault("auto", True)
    reserved = compaction.get("reserved")
    if profile_changed or type(reserved) is not int or not 0 <= reserved < profile.input:
        compaction["reserved"] = profile.reserved
    if json.dumps(config, sort_keys=True, ensure_ascii=False) == before:
        return False
    if not apply:
        return True
    backup = path.with_name(path.name + ".qwendesk-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
    backup.write_text(original, encoding="utf-8")
    try:
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        path.write_text(original, encoding="utf-8")
        raise
    return True


def command_line(script: Path, args: list[str]) -> str:
    """Fixed CLI args only. No shell-generated code or user-provided command fragments."""
    path = str(script)
    if any(c in path for c in '"%\r\n'):
        raise ControlError(tr('安装路径不能包含双引号、百分号或换行。'))
    quoted = []
    for index, arg in enumerate(args):
        if index and args[index - 1] == "--data-dir":
            if not Path(arg).is_absolute() or any(c in arg for c in '"%\r\n'):
                raise ControlError(tr('无效的模型数据目录。'))
            quoted.append(f'"{arg}"')
        elif index and args[index - 1] == "--gpu-roles" and arg == DUAL_GPU_ROLES:
            quoted.append(f'"{arg}"')
        elif arg and all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.=" for c in arg):
            quoted.append(arg)
        else:
            raise ControlError(tr('无效的内部命令参数。'))
    comspec = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe")
    return f'"{comspec}" /d /v:off /s /c ""{path}" {" ".join(quoted)}"'


class Controller:
    def __init__(self, root: Path, emit: Callable[[str], None] = lambda _: None,
                 url: str = "http://127.0.0.1:8080"):
        self.root = Path(root)
        self.emit = emit
        self.url = url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def agent(self) -> Path:
        return self.root / "agent"

    def health(self) -> dict | None:
        try:
            with self.opener.open(self.url + "/health", timeout=4) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise ControlError(tr('8080 接口返回的不是预期的服务状态。'))
            return result
        except urllib.error.HTTPError as exc:
            raise ControlError(tr('8080 状态接口返回 HTTP {0}，未按离线服务处理。', exc.code)) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError):
                return None
            raise ControlError(tr('Strata 状态暂时不可读：{0}', exc.reason)) from exc
        except ConnectionRefusedError:
            return None
        except (TimeoutError, ConnectionError) as exc:
            raise ControlError(tr('Strata 状态暂时不可读：{0}', exc)) from exc
        except (ValueError, UnicodeError) as exc:
            raise ControlError(tr('8080 接口返回了无法解析的状态，未操作该服务。')) from exc

    def desktop_processes(self) -> list[psutil.Process]:
        expected = os.path.normcase(os.path.abspath(self.agent / "desktop" / "OpenCode.exe"))
        found = []
        for process in psutil.process_iter(["name", "exe"]):
            try:
                if (process.info["name"] or "").lower() != "opencode.exe":
                    continue
                if os.path.normcase(os.path.abspath(process.info["exe"] or "")) == expected:
                    found.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return found

    def status(self) -> dict:
        free = shutil.disk_usage(self.root).free if self.root.is_dir() else None
        return {"health": self.health(), "desktop_count": len(self.desktop_processes()), "free_bytes": free}

    def plan(self, profile):
        free = shutil.disk_usage(self.root).free / 1024 ** 3
        self.emit(tr('磁盘剩余空间 {0} GiB。', format(free, '.1f')))
        self.run_cli([*profile.launch_args(self.root / "models"), "--plan"])
        return {"message": tr('预检完成。请核对日志中的待下载容量和内存提示。')}

    def run_cli(self, args: list[str], *, timeout=900) -> None:
        cli = self.root / "terminal" / "0.1.57" / "bin" / "strata.cmd"
        if not cli.is_file():
            raise ControlError(tr('找不到 Strata：{0}', cli))
        env = os.environ.copy()
        env.update(STRATA_DATA_DIR=str(self.root),
                   STRATA_COMPONENTS_DIR=str(self.root / "components"),
                   STRATA_UPDATE_FEED=str(self.root / "offline-feed"),
                   STRATA_SOURCE="modelscope", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        with subprocess.Popen(command_line(cli, args), cwd=self.root, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW) as process:
            def consume():
                assert process.stdout is not None
                for line in iter(process.stdout.readline, b""):
                    try:
                        text = line.decode("utf-8")
                    except UnicodeDecodeError:
                        text = line.decode(locale.getpreferredencoding(False), errors="replace")
                    if text.strip():
                        self.emit(text.rstrip())
            reader = threading.Thread(target=consume, daemon=True)
            reader.start()
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.terminate()
                process.wait(timeout=10)
                raise ControlError(tr('Strata 命令超过 15 分钟仍未结束。服务可能仍在加载，请检查日志。')) from exc
            reader.join(timeout=2)
            if code:
                raise ControlError(tr('Strata 命令退出码为 {0}，请查看运行日志。', code))

    def stop(self, expected=None) -> dict:
        health = self.health()
        active = running_profile(health)
        if health is not None and active is None:
            raise ControlError(tr('8080 端口是其他模型或上下文配置，未停止该服务。'))
        if active is not None and expected is not None and active != expected:
            raise ControlError(tr('当前运行的预设已改变，请刷新状态后再切换。'))
        if active is None:
            return {"message": tr('Strata 当前未连接。')}
        self.emit(tr('正在停止 Qwen 服务；模型文件会保留。'))
        self.run_cli(["launch", "--stop", "--preset", active.preset])
        for _ in range(30):
            if self.health() is None:
                return {"message": tr('Qwen 服务已停止，模型文件保留。')}
            time.sleep(1)
        raise ControlError(tr('停止命令结束，但服务仍在响应。请检查引擎日志。'))

    def start(self, profile=DEFAULT_PROFILE, force=False) -> dict:
        launcher = self.agent / "Start-Qwen-Desktop.cmd"
        config = self.agent / "config" / "opencode" / "opencode.json"
        if not launcher.is_file() or not config.is_file():
            raise ControlError(tr('找不到现有 OpenCode 启动器或配置，请在设置中检查安装位置。'))
        health = self.health()
        active = running_profile(health)
        if health is not None and active is None:
            raise ControlError(tr('8080 端口已有其他模型或上下文配置，请先自行停止。'))
        ready = matches(health, profile) and loaded(health)
        needs_sync = sync_config(config, profile, apply=False)
        if not ready and not matches(health, profile) and (health is not None or profile != DEFAULT_PROFILE):
            self.plan(profile)
        if (not ready or needs_sync) and self.desktop_processes():
            result = self.close(force=force)
            if result.get("needs_force"):
                return result
        if not ready:
            if health is not None:
                self.stop(expected=active)
            self.emit(tr('正在启动双卡 Qwen {0} / {1}。首次启动可能需要下载模型。', profile.quant, profile.context_name))
            self.run_cli([*profile.launch_args(self.root / "models"), "--yes", "--no-tui"], timeout=None)
            deadline = time.monotonic() + 600
            while True:
                health = self.health()
                if matches(health, profile) and loaded(health):
                    break
                if time.monotonic() > deadline:
                    raise ControlError(tr('10 分钟内模型未报告就绪，请检查 Strata 引擎日志。'))
                time.sleep(3)
        else:
            self.emit(tr('模型已经就绪，复用当前服务。'))
        changed = sync_config(config, profile)
        self.emit(tr('已同步 OpenCode {0} / {1} 并备份原配置。', profile.quant, profile.context_name) if changed else tr('OpenCode 已使用对应的模型与上下文。'))
        if self.desktop_processes():
            return {"message": tr('OpenCode 已在运行。') + (tr('上下文配置已更新，请退出后重新打开。') if changed else "")}
        subprocess.Popen(command_line(launcher, []), cwd=self.agent, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(30):
            if self.desktop_processes():
                return {"message": tr('Qwen 已就绪，OpenCode 已启动。')}
            time.sleep(0.5)
        raise ControlError(tr('模型已就绪，但 15 秒内未检测到 OpenCode 进程。请检查桌面启动器。'))

    def close(self, force: bool = False, stop_after: bool = False) -> dict:
        targets = self.desktop_processes()
        if targets:
            if force:
                self.emit(tr('按确认结束此安装目录的 OpenCode 进程。'))
                for process in targets:
                    try:
                        process.kill()
                    except psutil.NoSuchProcess:
                        pass
            else:
                pids = {process.pid for process in targets}
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
                user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
                user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
                @callback_type
                def callback(hwnd, _):
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value in pids:
                        user32.PostMessageW(hwnd, 0x0010, 0, 0)
                    return True
                user32.EnumWindows(callback, 0)
                self.emit(tr('已请求正常关闭 OpenCode。'))
            for _ in range(15):
                if not self.desktop_processes():
                    break
                time.sleep(0.2)
            if self.desktop_processes():
                if force:
                    raise ControlError(tr('OpenCode 仍有进程，请检查托盘或任务管理器。'))
                return {"needs_force": True, "stop_after": stop_after}
        if stop_after:
            return self.stop()
        return {"message": tr('OpenCode 已关闭。')}
