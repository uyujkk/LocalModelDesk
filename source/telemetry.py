"""Read-only Strata monitor adapter. Field mapping checked against upstream overview/client."""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request

MAX_BODY = 2 * 1024 * 1024
GIB = 1024 ** 3


def number(value):
    return float(value) if type(value) in (int, float) and 0 <= value <= 1e18 and math.isfinite(value) else None


def obj(value):
    return value if isinstance(value, dict) else {}


def rows(value, limit=20):
    return [row for row in value[:limit] if isinstance(row, dict)] if isinstance(value, list) else []


def total(values):
    return sum(values) if values and all(value is not None for value in values) else None


def maximum(values):
    valid = [value for value in values if value is not None]
    return max(valid) if valid else None


def rate(row):
    row = obj(row)
    direct = number(row.get("decode_tok_s"))
    if direct is not None:
        return direct
    out, duration = number(row.get("output_tokens")), number(row.get("decode_ms"))
    return out / (duration / 1000) if out is not None and out >= 2 and duration and duration > 0 else None


def normalize(raw, source="metrics"):
    if not isinstance(raw, dict):
        raise ValueError("监控接口未返回 JSON 对象。")
    if source == "telemetry":
        if raw.get("schema_version") != 1:
            raise ValueError("暂不支持该监控 schema 版本。")
        model, work, rates = obj(raw.get("model")), obj(raw.get("work")), obj(raw.get("rates"))
        slots = rows(work.get("slots"), 64)
        state = "reading" if any(s.get("state") == "reading" for s in slots) else "generating" if any(s.get("state") == "decoding" for s in slots) else "idle" if slots else "unknown"
        hw, static, engine = {}, {}, {}
        gpu_rows = rows(raw.get("devices"), 16)
        recent = rows(raw.get("recent"), 20)
        context, sampled = number(model.get("context")), number(raw.get("sampled_at"))
        held = [number(s.get("held_tokens")) for s in slots if s.get("state") != "idle"]
        used = maximum(held)
        speed = number(rates.get("prefill_tok_s")) if state == "reading" else number(rates.get("answer_tok_s"))
        speed_label = "提示处理速度" if state == "reading" else "回答速度"
        ram = obj(raw.get("ram"))
        ram_used, ram_total = number(ram.get("used")), number(ram.get("total"))
        queued = number(work.get("queued"))
        cpu = disk_read = disk_write = pcie = None
    else:
        if not isinstance(raw.get("live"), dict):
            raise ValueError("/metrics 结构与当前 Strata 仪表板不匹配。")
        live, engine, hw, static = raw["live"], obj(raw.get("engine")), obj(raw.get("hardware")), obj(raw.get("hardware_static"))
        state = "error" if engine.get("error") else live.get("state", "unknown")
        recent = rows(raw.get("requests"), 20)
        gpu_rows = rows(hw.get("gpus"), 16)
        # Never duplicate a card-0 aggregate to make a fictitious second GPU.
        if not gpu_rows and static.get("gpu_count") == 1:
            gpu_rows = [{"index": 0, "name": static.get("gpu_name"),
                         "mem_used": hw.get("gpu_mem_used"), "mem_total": hw.get("gpu_mem_total"),
                         "util": hw.get("gpu_util"), "temp": hw.get("gpu_temp"), "power": hw.get("gpu_power")}]
        context, sampled = number(engine.get("max_context")), number(raw.get("time"))
        prompt, generated = number(live.get("prompt_tokens")), number(live.get("generated"))
        if state in ("reading", "generating") and prompt is not None and generated is not None:
            used = prompt + generated
        elif recent:
            prompt, generated = number(recent[0].get("prompt_tokens")), number(recent[0].get("output_tokens"))
            used = prompt + generated if prompt is not None and generated is not None else None
        else:
            used = None
        if state == "reading":
            speed, speed_label = number(live.get("prefill_tok_s_mean")), "提示处理速度"
        elif state == "generating":
            speed, speed_label = number(live.get("tok_s")), "当前生成速度"
        else:
            speed = rate(raw.get("totals"))
            speed_label = "本轮平均生成速度"
            if speed is None:
                speed, speed_label = rate(recent[0]) if recent else None, "最近请求生成速度"
        ram_used, ram_total = number(hw.get("ram_used")), number(hw.get("ram_total"))
        queued = number(live.get("queued"))
        cpu = number(hw.get("cpu_pct") if hw.get("cpu_pct") is not None else hw.get("cpu"))
        disk_read, disk_write, pcie = number(hw.get("disk_read_mb")), number(hw.get("disk_write_mb")), number(hw.get("gpu_pcie_rx_mb"))
    gpus = []
    seen = set()
    for row in gpu_rows:
        index = row.get("index")
        if type(index) is not int or index < 0 or index in seen:
            continue
        seen.add(index)
        gpus.append({"index": index, "name": str(row.get("name") or "未知显卡")[:100],
                     **{key: number(row.get(key)) for key in ("util", "mem_used", "mem_total", "temp", "power")}})
    return {"source": source, "sampled_at": sampled, "state": state,
            "speed": speed, "speed_label": speed_label, "queued": queued,
            "gpus": gpus, "gpu_util": maximum([g["util"] for g in gpus]),
            "gpu_temp": maximum([g["temp"] for g in gpus]),
            "gpu_power": total([g["power"] for g in gpus]),
            "vram_used": total([g["mem_used"] for g in gpus]),
            "vram_total": total([g["mem_total"] for g in gpus]),
            "ram_used": ram_used, "ram_total": ram_total,
            "cpu": cpu, "disk_read": disk_read, "disk_write": disk_write, "pcie": pcie,
            "context_used": used, "context_total": context,
            "context_label": "当前请求上下文" if state in ("reading", "generating") else "最近请求上下文",
            "experts": number(engine.get("expert_slots")),
            "experts_mib": number(engine.get("expert_cache_mib")),
            "recent": [{"time": number(r.get("time")), "state": "错误" if r.get("error") else str(r.get("finish_reason") or r.get("finish") or "完成")[:30],
                        "prompt": number(r.get("prompt_tokens")), "reused": number(r.get("reused")),
                        "output": number(r.get("output_tokens")), "speed": rate(r), "duration": number(r.get("duration_s"))}
                       for r in recent]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(*, raw_snapshot=False):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    for path, source in [("/metrics", "metrics"), ("/v1/telemetry", "telemetry")]:
        try:
            request = urllib.request.Request("http://127.0.0.1:8080" + path, headers={"Accept": "application/json"})
            with opener.open(request, timeout=3) as response:
                content_type = response.headers.get("Content-Type", "")
                if "json" not in content_type:
                    raise ValueError("监控接口未返回 JSON，请检查当前 Strata 版本。")
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ValueError("监控响应超过 2 MiB，已停止读取。")
            payload = json.loads(body)
            if raw_snapshot:
                return payload, source
            return normalize(payload, source)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise ValueError(f"监控接口 HTTP {exc.code}。") from exc
    raise ValueError("该服务没有可用的监控接口；可以尝试打开原版监控。")
