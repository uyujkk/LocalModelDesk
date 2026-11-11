"""Windows OpenCode desktop discovery and its V2 configuration and service."""
from __future__ import annotations

import base64
from copy import deepcopy
import ctypes
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from i18n import tr


MIN_GUI_VERSION = (2, 2, 6)
CHART_PLUGIN = "strata-charts"
CHART_ASSETS = Path(__file__).resolve().parent / "assets" / CHART_PLUGIN
CHART_FILES = ("package.json", "gui-experimental.cjs", "Chart-Instructions.md")


class OpenCodeError(RuntimeError):
    pass


def file_version(path: Path) -> tuple[int, int, int]:
    """Read the Windows fixed version resource without running the executable."""
    if os.name != "nt" or not path.is_file():
        return (0, 0, 0)
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    size_of = version.GetFileVersionInfoSizeW
    size_of.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    size_of.restype = wintypes.DWORD
    size = size_of(str(path), None)
    if not size:
        return (0, 0, 0)
    data = ctypes.create_string_buffer(size)
    read = version.GetFileVersionInfoW
    read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    query = version.VerQueryValueW
    query.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                      ctypes.POINTER(wintypes.UINT)]
    address = ctypes.c_void_p()
    length = wintypes.UINT()
    if not read(str(path), 0, size, data) or not query(data, "\\", ctypes.byref(address), ctypes.byref(length)):
        return (0, 0, 0)
    if length.value < 52 or not address.value:
        return (0, 0, 0)
    info = ctypes.cast(address, ctypes.POINTER(wintypes.DWORD))
    if info[0] != 0xFEEF04BD:
        return (0, 0, 0)
    major_minor, patch_build = info[4], info[5]
    return (major_minor >> 16, major_minor & 0xFFFF, patch_build >> 16)


def installed_desktops():
    if os.name != "nt":
        return
    import winreg

    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, uninstall) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                            name = winreg.QueryValueEx(entry, "DisplayName")[0]
                            if re.fullmatch(r"OpenCode(?:\s+\d[\w. -]*)?", name):
                                directory = winreg.QueryValueEx(entry, "InstallLocation")[0]
                                if directory:
                                    yield Path(directory) / "OpenCode.exe"
                    except OSError:
                        continue
        except OSError:
            continue
    programs = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs"
    for name in ("ai.opencode.desktop", "OpenCode", "@opencodedesktop"):
        yield programs / name / "OpenCode.exe"
    if os.environ.get("ProgramFiles"):
        yield Path(os.environ["ProgramFiles"]) / "OpenCode" / "OpenCode.exe"


@dataclass(frozen=True)
class Desktop:
    exe: Path
    version: tuple[int, int, int]

    @property
    def v2(self):
        return self.version[0] >= 2

    @property
    def cli(self):
        return self.exe.parent / "resources" / "opencode-cli.exe"


def find_desktop(agent: Path, override: Path | None = None) -> Desktop | None:
    def inspect(path):
        if path.is_file() and (path.parent / "resources" / "app.asar").is_file():
            version = file_version(path)
            if version != (0, 0, 0):
                return Desktop(path.resolve(), version)
        return None

    if override is not None:
        return inspect(override if override.is_absolute() else agent.parent / override)
    legacy = agent / "desktop" / "OpenCode.exe"
    paths = [legacy, *installed_desktops()]
    return max((candidate for path in dict.fromkeys(paths) if (candidate := inspect(path))
                and (path == legacy or candidate.v2)), key=lambda candidate: candidate.version, default=None)


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: dict):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


@dataclass(frozen=True)
class BackgroundService:
    id: str
    pid: int
    url: str
    password: str

    def get(self, route: str):
        auth = base64.b64encode(f"opencode:{self.password}".encode()).decode("ascii")
        opener = build_opener(ProxyHandler({}))
        request = Request(self.url + route, headers={"Authorization": "Basic " + auth})
        try:
            with opener.open(request, timeout=1) as response:
                return json.load(response)
        except (OSError, URLError, ValueError):
            return None


def background_service() -> BackgroundService | None:
    # OpenCode 2.2.7 Desktop uses its Windows user's stock service registration.
    registration = Path.home() / ".local" / "state" / "opencode" / "service.json"
    try:
        record = read_object(registration)
        service = BackgroundService(record["id"], record["pid"], record["url"], record["password"])
        origin = urlsplit(service.url)
        if (not isinstance(service.id, str) or not isinstance(service.password, str)
                or type(service.pid) is not int or service.pid <= 0
                or origin.scheme != "http" or origin.hostname not in ("127.0.0.1", "localhost", "::1")
                or origin.username or origin.password or not origin.port
                or origin.path not in ("", "/") or origin.query or origin.fragment):
            return None
        service = BackgroundService(service.id, service.pid, service.url.rstrip("/"), service.password)
        info = service.get("/api/info")
        if isinstance(info, dict) and (info.get("data", info) or {}).get("pid") == service.pid:
            return service
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


class OpenCodeV2:
    def __init__(self, desktop: Desktop, agent: Path, url: str):
        self.desktop = desktop
        self.agent = agent.resolve()
        self.profile = self.agent / "opencode-v2"
        self.config = self.profile / "config" / "opencode" / "opencode.json"
        self.plugin = self.profile / "plugins" / CHART_PLUGIN
        self.receipt = self.profile / "service.json"
        self.url = url

    def validate(self):
        if self.desktop.version < MIN_GUI_VERSION or not self.desktop.cli.is_file():
            raise OpenCodeError(tr("此适配需要 OpenCode Desktop 2.2.6 或更新版本及其内置后端。"))
        if any(not (CHART_ASSETS / file).is_file() for file in CHART_FILES):
            raise OpenCodeError(tr("图表插件文件不完整，请重新解压 Local Model Desk。"))

    def sync(self, profile, *, apply=True):
        if self.config.exists():
            config = read_object(self.config)
            before = deepcopy(config)
        else:
            before = None
            config = {"share": "disabled", "lsp": False, "formatter": False}
            legacy = self.agent / "config" / "opencode" / "opencode.json"
            try:
                previous = read_object(legacy)
            except (OSError, ValueError):
                previous = {}
            for key in ("permission", "permissions"):
                if key in previous:
                    config[key] = previous[key]
            if "permission" not in config and "permissions" not in config:
                config["permission"] = {"*": "ask", "read": "allow", "glob": "allow",
                                        "grep": "allow", "list": "allow", "edit": "allow",
                                        "external_directory": "ask", "task": "deny",
                                        "webfetch": "deny", "websearch": "deny"}

        selected = f"strata/{profile.model}"
        strata = config.setdefault("providers", {}).setdefault("strata", {})
        strata.setdefault("name", "Strata (local)")
        strata["package"] = "@opencode/ai/providers/openai-compatible"
        settings = strata.setdefault("settings", {})
        settings["baseURL"] = self.url + "/v1"
        models = strata.setdefault("models", {})
        model = models.setdefault(profile.model, {})
        previous_limit = model.get("limit", {}).get("context")
        model.setdefault("name", f"Qwen3.8-Flash-Next {profile.quant} via Strata")
        capabilities = model.setdefault("capabilities", {})
        capabilities.update(tools=True, input=["text", "image"] if profile.images else ["text"])
        capabilities.setdefault("output", ["text"])
        model.setdefault("limit", {}).update(context=profile.context, input=profile.input, output=profile.output)
        changed_profile = config.get("model") != selected or previous_limit != profile.context
        config.update(model=selected, small_model=selected)
        config.setdefault("enabled_providers", ["strata"])
        compaction = config.setdefault("compaction", {})
        compaction.setdefault("auto", True)
        if changed_profile or "buffer" not in compaction:
            compaction["buffer"] = profile.reserved
        # The V1 field has a different meaning. V2's supported setting is buffer.
        compaction.pop("reserved", None)
        plugin_path = str(self.plugin)
        plugins = config.setdefault("plugins", [])
        if not any((item.get("package") if isinstance(item, dict) else item) == plugin_path for item in plugins):
            plugins.append({"package": plugin_path})
        instructions = config.setdefault("instructions", [])
        instruction_path = str(self.plugin / "Chart-Instructions.md")
        if instruction_path not in instructions:
            instructions.append(instruction_path)

        assets = {file: (CHART_ASSETS / file).read_bytes() for file in CHART_FILES}
        changed_assets = {file: data for file, data in assets.items()
                          if not (self.plugin / file).is_file() or (self.plugin / file).read_bytes() != data}
        changed = before != config or bool(changed_assets)
        if apply:
            for file, data in changed_assets.items():
                atomic_write(self.plugin / file, data)
            if before != config:
                if before is not None:
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                    atomic_write(self.config.with_name(f"opencode.json.qwendesk-{stamp}.bak"), self.config.read_bytes())
                write_json(self.config, config)
        return changed

    def environment(self) -> dict:
        env = os.environ.copy()
        for key in ("XDG_STATE_HOME", "OPENCODE_CONFIG_CONTENT", "OPENCODE_DESKTOP_SERVER_CHANNEL",
                    "OPENCODE_DESKTOP_TEST_ROOT", "OPENCODE_TEST_ONBOARDING", "ELECTRON_RUN_AS_NODE",
                    "OPENCODE_SIDECAR_V2", "OPENCODE_PURE"):
            env.pop(key, None)
        env.update(XDG_CONFIG_HOME=str(self.profile / "config"),
                   XDG_DATA_HOME=str(self.profile / "data"),
                   XDG_CACHE_HOME=str(self.profile / "cache"),
                   OPENCODE_CONFIG_DIR=str(self.config.parent),
                   OPENCODE_CONFIG=str(self.config),
                   OPENCODE_CONFIG_PROJECT_DISABLE="1", OPENCODE_DISABLE_MODELS_FETCH="1")
        git = self.agent / "git" / "bin" / "bash.exe"
        extra_paths = []
        if git.is_file():
            env["OPENCODE_GIT_BASH_PATH"] = str(git)
            extra_paths.append(str(self.agent / "git" / "cmd"))
        terminal = self.agent.parent / "terminal"
        if terminal.is_dir():
            versions = sorted((item for item in terminal.iterdir()
                               if item.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", item.name)),
                              key=lambda item: tuple(map(int, item.name.split("."))), reverse=True)
            python = next((item / "python" for item in versions if (item / "python" / "python.exe").is_file()), None)
            if python:
                extra_paths.append(str(python))
        if extra_paths:
            env["PATH"] = os.pathsep.join([*extra_paths, env.get("PATH", "")])
        return env

    def owns(self, service: BackgroundService) -> bool:
        try:
            return read_object(self.receipt) == {"id": service.id, "pid": service.pid}
        except (OSError, ValueError):
            return False

    def ready(self, service: BackgroundService, profile) -> bool:
        models = service.get("/api/model")
        if not isinstance(models, dict) or not any(
            model.get("providerID") == "strata" and model.get("id") == profile.model
            and model.get("limit", {}).get("context") == profile.context
            and ("image" in model.get("capabilities", {}).get("input", [])) == profile.images
            for model in models.get("data", [])
        ):
            return False
        bundle = service.get(f"/api/plugin/gui?id={CHART_PLUGIN}")
        if not isinstance(bundle, dict):
            return False
        code = bundle.get("data", bundle).get("code")
        return isinstance(code, str) and code.encode("utf-8") == (self.plugin / "gui-experimental.cjs").read_bytes()

    def remember(self, service: BackgroundService):
        write_json(self.receipt, {"id": service.id, "pid": service.pid})

    def stop_service(self, expected: BackgroundService):
        if background_service() != expected:
            raise OpenCodeError(tr("OpenCode 后台服务已变化，请重试。"))
        result = subprocess.run([str(self.desktop.cli), "service", "stop"], env=self.environment(),
                                cwd=self.agent, capture_output=True, timeout=25,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        if result.returncode or background_service() is not None:
            raise OpenCodeError(tr("未能停止 OpenCode 后台服务，请先在 OpenCode 中结束运行中的任务。"))

    def launch(self):
        return subprocess.Popen([str(self.desktop.exe)], cwd=self.agent, env=self.environment(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
