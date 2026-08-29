"""아이템 개조 매크로 - 성공할 때까지 반복.

    python -m src.macro                # 기본 config.yaml
    python -m src.macro --config x.yaml --max 50 --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from .adb import Adb
from .ocr import Ocr
from .runner import ROOT, load_config, run_steps


def parse_gold(text: str) -> int | None:
    best = None
    for m in re.finditer(r"\d[\d,]{3,}", text):
        v = int(m.group(0).replace(",", ""))
        if best is None or v > best:
            best = v
    return best


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--max", type=int, default=None, help="최대 시도 횟수 (config 값 override)")
    ap.add_argument("--dry-run", action="store_true", help="탭/스와이프 없이 화면만 읽어 판정 흐름 확인")
    args = ap.parse_args()

    cfg = load_config(args.config)
    timing = cfg["timing"]
    safety = cfg["safety"]
    rcfg = cfg["result"]
    max_attempts = args.max or safety.get("max_attempts", 100)

    adb = Adb(cfg)
    if args.dry_run:
        log("DRY-RUN: 입력을 보내지 않습니다.")
        adb.tap = lambda *a, **k: log(f"  tap{a}")          # type: ignore
        adb.swipe = lambda *a, **k: log(f"  swipe{a}")      # type: ignore
        adb.key = lambda *a, **k: log(f"  key{a}")          # type: ignore

    adb.connect()
    log(f"연결됨: {adb.serial}")
    ocr = Ocr(cfg)

    if adb.package and not adb.is_game_foreground():
        log(f"경고: 게임({adb.package})이 포그라운드가 아닙니다. 계속하려면 Enter, 중단하려면 Ctrl+C")
        input()

    shots = ROOT / "captures" / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    shots.mkdir(parents=True, exist_ok=True)

    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        log(f"--- 시도 {attempt}/{max_attempts} ---")

        # 골드 안전장치
        min_gold = safety.get("min_gold", 0)
        if min_gold:
            img = adb.screencap()
            gtxt = ocr.text_dump(img, safety.get("gold_region"))
            gold = parse_gold(gtxt)
            if gold is not None and gold < min_gold:
                log(f"골드 {gold} < 최소 {min_gold}. 중단.")
                return 2

        # 개조 시도
        run_steps(adb, ocr, cfg["attempt_sequence"], timing)
        adb.wait(timing.get("after_modify", 1.8))

        # 결과 판정
        img = adb.screencap()
        import cv2  # noqa
        cv2.imwrite(str(shots / f"{attempt:04d}.png"), img)

        hit = ocr.find_any(img, rcfg["success_keywords"], rcfg.get("region"))
        if hit:
            kw, ln = hit
            log(f"✅ 개조 성공! ('{ln.text}') — {attempt}회차. 중단합니다.")
            _alert(adb)
            return 0

        fail = ocr.find_any(img, rcfg["fail_keywords"], rcfg.get("region"))
        if fail:
            kw, ln = fail
            log(f"❌ 실패 ('{ln.text}'). 팝업 닫고 재시도.")
            run_steps(adb, ocr, rcfg.get("dismiss_sequence", []), timing)
            adb.wait(timing.get("loop_idle", 0.4))
            continue

        # 성공도 실패도 못 읽음
        log("⚠ 결과 텍스트를 인식하지 못했습니다.")
        log(ocr.text_dump(img, rcfg.get("region")))
        if safety.get("stop_on_unknown_screen", True):
            log(f"중단. 스크린샷: {shots / f'{attempt:04d}.png'}")
            return 3
        run_steps(adb, ocr, rcfg.get("dismiss_sequence", []), timing)

    log(f"최대 시도 횟수({max_attempts}) 도달. 중단.")
    return 1


def _alert(adb: Adb) -> None:
    try:
        for _ in range(3):
            print("\a", end="", flush=True)
            time.sleep(0.3)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
