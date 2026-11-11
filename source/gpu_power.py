"""Read and change NVIDIA board power limits through the installed driver."""
from i18n import tr

import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
import subprocess
import winreg


@dataclass(frozen=True)
class GPU:
    index: str
    name: str
    uuid: str
    draw: Decimal | None
    current: Decimal | None
    default: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None

    @property
    def adjustable(self):
        return (self.current is not None and self.minimum is not None
                and self.maximum is not None and self.minimum < self.maximum)

    def accepts(self, watts):
        return (self.adjustable and watts is not None and watts.is_finite()
                and self.minimum <= watts <= self.maximum)


def watts_value(value):
    if not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,3})?", value):
        return None
    return Decimal(value)


def nvidia_smi():
    system_path = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(system_path, len(system_path))
    paths = []
    if 0 < length < len(system_path):
        paths.append(Path(system_path.value) / "nvidia-smi.exe")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion",
                            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            program_files, _ = winreg.QueryValueEx(key, "ProgramFilesDir")
        paths.append(Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe")
    except OSError:
        pass
    for path in paths:
        if path.is_file():
            return path
    raise RuntimeError(tr('在 NVIDIA 驱动的标准安装目录找不到 nvidia-smi.exe。'))


def query_gpus():
    result = subprocess.run(
        [str(nvidia_smi()), "--query-gpu=index,name,uuid,power.draw,power.limit,"
         "power.default_limit,power.min_limit,power.max_limit", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, errors="replace", timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or tr('NVIDIA 查询失败。'))
    return parse_gpus(result.stdout)


def parse_gpus(output):
    cards = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row:
            continue
        if len(row) != 8 or not row[0].strip().isdigit() or not re.fullmatch(
            r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", row[2].strip()
        ):
            raise RuntimeError(tr('无法解析 NVIDIA 驱动返回的显卡数据。'))
        index, name, uuid, *values = (value.strip() for value in row)
        cards.append(GPU(index, name, uuid, *(watts_value(value) for value in values)))
    return cards


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def run_elevated(path, args):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # NOCLOSEPROCESS | NOASYNC | FLAG_NO_UI
    info.lpVerb = "runas"
    info.lpFile = str(path)
    info.lpDirectory = str(path.parent)
    info.lpParameters = subprocess.list2cmdline(args)
    info.nShow = 0  # SW_HIDE
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise RuntimeError(tr('已取消 Windows 管理员授权。'))
        raise ctypes.WinError(error)
    if not info.hProcess:
        raise RuntimeError(tr('无法取得 NVIDIA 命令的进程句柄，请刷新确认当前上限。'))
    try:
        wait = kernel.WaitForSingleObject(info.hProcess, 20000)
        if wait == 258:
            raise RuntimeError(tr('NVIDIA 命令尚未确认完成，请稍后刷新功耗上限。'))
        if wait != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value:
            raise RuntimeError(tr('NVIDIA 驱动未接受功耗设置（退出码 {0}）。请检查设备是否支持修改。', code.value))
    finally:
        kernel.CloseHandle(info.hProcess)


def set_power_limit(uuid, target):
    card = next((item for item in query_gpus() if item.uuid == uuid), None)
    if card is None or not card.accepts(target):
        raise RuntimeError(tr('显卡已变化，或目标值超出驱动允许的范围。请刷新后重试。'))
    if card.current != target:
        run_elevated(nvidia_smi(), ["-i", card.uuid, "-pl", format(target, "f")])
    cards = query_gpus()
    actual = next((item.current for item in cards if item.uuid == uuid), None)
    if actual != target:
        raise RuntimeError(tr('请求 {0} W，驱动读回 {1}。', target, actual if actual is not None else tr('不可用')))
    return cards
