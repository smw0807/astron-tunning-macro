"""아이템 개조 매크로 - 성공할 때까지 반복.

CLI:
    python -m src.macro                # 기본 config.yaml
    python -m src.macro --config x.yaml --max 50 --dry-run

GUI 에서는 Macro 클래스를 직접 사용한다.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .adb import Adb
from .ocr import Ocr
from .runner import ROOT, load_config, run_steps

# 실행 결과 코드
OK = 0            # 개조 성공
MAX_REACHED = 1   # 최대 시도 도달
LOW_GOLD = 2      # 골드 부족
UNKNOWN = 3       # 결과 화면 인식 실패
STOPPED = 4       # 사용자 중단


def parse_gold(text: str) -> int | None:
    best = None
    for m in re.finditer(r"\d[\d,]{3,}", text):
        v = int(m.group(0).replace(",", ""))
        if best is None or v > best:
            best = v
    return best


class Macro:
    """개조 반복 루프. GUI/CLI 공용.

    on_log(str)         : 로그 콜백
    should_stop() -> bool: True 면 즉시 중단
    on_progress(done, total, status): 진행 상황 콜백
    on_shot(bgr_img, tag): 매 시도 결과 스크린샷 콜백
    """

    def __init__(
        self,
        cfg: dict,
        *,
        dry_run: bool = False,
        on_log: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_shot: Callable[[object, str], None] | None = None,
    ):
        self.cfg = cfg
        self.dry_run = dry_run
        self._log = on_log or (lambda m: print(m, flush=True))
        self._stop = should_stop or (lambda: False)
        self._progress = on_progress or (lambda a, b, c: None)
        self._on_shot = on_shot
        self.adb: Adb | None = None
        self.ocr: Ocr | None = None

    def log(self, msg: str) -> None:
        self._log(f"[{datetime.now():%H:%M:%S}] {msg}")

    # ------------------------------------------------------------------
    def setup(self) -> None:
        self.adb = Adb(self.cfg)
        if self.dry_run:
            self.log("DRY-RUN: 입력(탭/스와이프)을 보내지 않습니다.")
            self.adb.tap = lambda *a, **k: self.log(f"  tap{a}")      # type: ignore
            self.adb.swipe = lambda *a, **k: self.log(f"  swipe{a}")  # type: ignore
            self.adb.key = lambda *a, **k: self.log(f"  key{a}")      # type: ignore
        self.adb.connect()
        self.log(f"연결됨: {self.adb.serial}")
        self.ocr = Ocr(self.cfg)
        self.log("OCR 초기화 완료")

    # ------------------------------------------------------------------
    def run(self, max_attempts: int | None = None) -> int:
        if self.adb is None or self.ocr is None:
            self.setup()
        assert self.adb and self.ocr
        cfg = self.cfg
        timing = cfg["timing"]
        safety = cfg["safety"]
        rcfg = cfg["result"]
        total = max_attempts or safety.get("max_attempts", 100)

        if self.adb.package and not self.adb.is_game_foreground():
            self.log(f"경고: 게임({self.adb.package})이 포그라운드가 아닙니다.")

        shots = ROOT / "captures" / f"run_{datetime.now():%Y%m%d_%H%M%S}"
        shots.mkdir(parents=True, exist_ok=True)
        min_gold = safety.get("min_gold", 0)

        attempt = 0
        while attempt < total:
            if self._stop():
                self.log("사용자 중단.")
                return STOPPED
            attempt += 1
            self._progress(attempt, total, "시도 중")
            self.log(f"--- 시도 {attempt}/{total} ---")

            if min_gold:
                img = self.adb.screencap()
                gold = parse_gold(self.ocr.text_dump(img, safety.get("gold_region")))
                if gold is not None and gold < min_gold:
                    self.log(f"골드 {gold:,} < 최소 {min_gold:,}. 중단.")
                    return LOW_GOLD

            run_steps(self.adb, self.ocr, cfg["attempt_sequence"], timing, self._stop)
            self.adb.wait(timing.get("after_modify", 1.8))

            img = self.adb.screencap()
            import cv2
            path = shots / f"{attempt:04d}.png"
            cv2.imwrite(str(path), img)
            if self._on_shot:
                self._on_shot(img, f"시도 {attempt}")

            hit = self.ocr.find_any(img, rcfg["success_keywords"], rcfg.get("region"))
            if hit:
                _, ln = hit
                self.log(f"✅ 개조 성공! ('{ln.text}') — {attempt}회차. 중단합니다.")
                self._progress(attempt, total, "성공")
                self._alert()
                return OK

            fail = self.ocr.find_any(img, rcfg["fail_keywords"], rcfg.get("region"))
            if fail:
                _, ln = fail
                self.log(f"❌ 실패 ('{ln.text}'). 팝업 닫고 재시도.")
                run_steps(self.adb, self.ocr, rcfg.get("dismiss_sequence", []), timing, self._stop)
                self.adb.wait(timing.get("loop_idle", 0.4))
                continue

            self.log("⚠ 결과 텍스트를 인식하지 못했습니다.")
            self.log(self.ocr.text_dump(img, rcfg.get("region")) or "  (인식된 텍스트 없음)")
            if safety.get("stop_on_unknown_screen", True):
                self.log(f"중단. 스크린샷: {path}")
                return UNKNOWN
            run_steps(self.adb, self.ocr, rcfg.get("dismiss_sequence", []), timing, self._stop)

        self.log(f"최대 시도 횟수({total}) 도달. 중단.")
        return MAX_REACHED

    def _alert(self) -> None:
        try:
            for _ in range(3):
                print("\a", end="", flush=True)
                time.sleep(0.3)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--max", type=int, default=None, help="최대 시도 횟수 (config 값 override)")
    ap.add_argument("--dry-run", action="store_true", help="입력 없이 판정 흐름만 확인")
    args = ap.parse_args()

    m = Macro(load_config(args.config), dry_run=args.dry_run)
    return m.run(args.max)


if __name__ == "__main__":
    sys.exit(main())
