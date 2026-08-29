"""공용 로더 + attempt_sequence 실행기."""
from __future__ import annotations

from pathlib import Path

import yaml

from .adb import Adb
from .ocr import Ocr

ROOT = Path(__file__).resolve().parent.parent


def config_path(path: str | Path = "config.yaml") -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_config(path: str | Path = "config.yaml") -> dict:
    return yaml.safe_load(config_path(path).read_text(encoding="utf-8"))


_HEADER = (
    "# 아스트론 아이템 개조 매크로 설정\n"
    "# 좌표는 모두 캡처 해상도(screencap) 기준 픽셀값. GUI(python -m src.gui)로 편집 권장.\n"
    "# 이 파일은 GUI 저장 시 재생성되므로 주석은 유지되지 않습니다.\n\n"
)


def save_config(cfg: dict, path: str | Path = "config.yaml") -> Path:
    p = config_path(path)
    body = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False)
    p.write_text(_HEADER + body, encoding="utf-8")
    return p


def run_steps(adb: Adb, ocr: Ocr, steps: list[dict], timing: dict,
              should_stop=None, ctx: dict | None = None, log=None) -> None:
    """시퀀스 스텝 실행.

    ctx["slot_xy"] 가 주어지면 {tap_slot: true} 스텝이 그 좌표를 탭한다.
    """
    stop = should_stop or (lambda: False)
    ctx = ctx or {}
    say = log or (lambda m: None)
    for step in steps:
        if stop():
            return
        if "tap_slot" in step:
            xy = ctx.get("slot_xy")
            if xy and (xy[0] or xy[1]):
                adb.tap(xy[0], xy[1])
                adb.wait(timing.get("after_tap", 0.6))
            else:
                say("  (tap_slot: 슬롯 좌표 미설정 — 건너뜀)")
        elif "tap" in step:
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
