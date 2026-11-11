"""Synthetic data used only by offline validation, never by normal monitoring."""
import time


def sample():
    gib = 1024 ** 3
    now = time.time()
    return {"time": now, "live": {"state": "generating", "tok_s": 96.8,
            "prompt_tokens": 24480, "generated": 1536, "queued": 0},
        "engine": {"model": "qwen3.8-flash-next-iq2_xs", "max_context": 262144,
                   "expert_slots": 6896, "expert_cache_mib": 9200},
        "hardware": {"gpus": [
            {"index": 0, "name": "RTX 3080 Ti", "util": 68, "mem_used": 11.1*gib, "mem_total": 12*gib, "temp": 64, "power": 168},
            {"index": 1, "name": "RTX 3080", "util": 57, "mem_used": 10.8*gib, "mem_total": 12*gib, "temp": 60, "power": 155}],
            "cpu_pct": 14, "ram_used": 47.2*gib, "ram_total": 64*gib,
            "disk_read_mb": 12.1, "disk_write_mb": 0.9, "gpu_pcie_rx_mb": 2410},
        "requests": [{"time": now-30*i, "finish_reason": "stop", "prompt_tokens": 1200+i*40,
            "reused": 600, "output_tokens": 1947, "decode_ms": 20000,
            "duration_s": 22.3+i} for i in range(6)]}
