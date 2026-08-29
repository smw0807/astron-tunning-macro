"""공용 로더 + attempt_sequence 실행기."""
from __future__ import annotations

from pathlib import Path

import yaml

from .adb import Adb
from .ocr import Ocr

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path = "config.yaml") -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def run_steps(adb: Adb, ocr: Ocr, steps: list[dict], timing: dict) -> None:
    """attempt_sequence / dismiss_sequence 스텝 실행."""
    for step in steps:
        if "tap" in step:
            x, y = step["tap"]
            adb.tap(x, y)
            adb.wait(timing.get("after_tap", 0.6))
        elif "swipe" in step:
            v = step["swipe"]
            ms = v[4] if len(v) > 4 else 300
            adb.swipe(v[0], v[1], v[2], v[3], ms)
            adb.wait(timing.get("after_tap", 0.6))
        elif "tap_if_text" in step:
            t = step["tap_if_text"]
            img = adb.screencap()
            ln = ocr.find(img, t["text"], t.get("region"))
            if ln:
                adb.tap(ln.cx, ln.cy)
                adb.wait(timing.get("after_confirm", 1.2))
        elif "key" in step:
            adb.key(step["key"])
            adb.wait(timing.get("after_tap", 0.6))
        elif "wait" in step:
            adb.wait(float(step["wait"]))
        else:
            raise ValueError(f"알 수 없는 스텝: {step}")
