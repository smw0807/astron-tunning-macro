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
from .runner import ROOT, SequenceError, load_config, run_steps

# 실행 결과 코드
OK = 0            # 목표 슬롯 수 모두 완료
MAX_REACHED = 1   # 최대 시도 도달
LOW_GOLD = 2      # 골드 부족 / 구매 실패
UNKNOWN = 3       # 결과 화면 인식 실패
STOPPED = 4       # 사용자 중단

# 슬롯 처리 결과
_DONE = "done"        # 목표 레벨 도달
_BRICKED = "bricked"  # 연속 실패로 개조 불가 → 판매 후 재구매
_ABORT = "abort"      # 전체 중단 (코드 동반)


def parse_gold(text: str) -> int | None:
    best = None
    for m in re.finditer(r"\d[\d,]{3,}", text):
        v = int(m.group(0).replace(",", ""))
        if best is None or v > best:
            best = v
    return best


DEFAULT_LEVEL_PATTERN = r"(?:lv|레벨|레 벨)\s*[.:]?\s*([1-8])"


def parse_level(text: str, pattern: str = DEFAULT_LEVEL_PATTERN) -> int | None:
    """OCR 텍스트에서 아이템 레벨(1~8)을 추출."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # 패턴 실패 시: 영역 안에 홀로 있는 1~8 숫자
    digits = re.findall(r"\b([1-8])\b", text)
    if len(digits) == 1:
        return int(digits[0])
    return None


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
        self._timing = cfg["timing"]
        self._safety = cfg["safety"]
        self._rcfg = cfg["result"]
        self._max = max_attempts or self._safety.get("max_attempts", 100)

        if self.adb.package and not self.adb.is_game_foreground():
            self.log(f"경고: 게임({self.adb.package})이 포그라운드가 아닙니다.")

        mod = cfg.get("modify", {})
        self._fail_limit = int(mod.get("fail_limit", 3))
        self._target_level = int(mod.get("target_level", 8))
        self._start_level = int(mod.get("start_level", 1))
        lr = mod.get("level_region")
        if lr and (len(lr) != 4 or lr[0] >= lr[2] or lr[1] >= lr[3]):
            lr = None
        self._level_region = lr
        self._level_pattern = mod.get("level_pattern") or DEFAULT_LEVEL_PATTERN

        self._fail_seq = self._rcfg.get("fail_sequence", [])
        self._succ_seq = self._rcfg.get("success_sequence", [])
        self._locked_kw = self._rcfg.get("locked_keywords", [])

        slots = cfg.get("slots", {})
        target_count = int(slots.get("target_count", 1))
        positions = slots.get("positions", []) or []

        self._shots = ROOT / "captures" / f"run_{datetime.now():%Y%m%d_%H%M%S}"
        self._shots.mkdir(parents=True, exist_ok=True)
        self._attempt = 0

        for slot_no in range(1, target_count + 1):
            slot_xy = positions[slot_no - 1] if slot_no - 1 < len(positions) else None
            self.log(f"════════ 슬롯 {slot_no}/{target_count} ════════"
                     + ("" if slot_xy else "  (슬롯 좌표 미설정!)"))
            while True:
                if self._stop():
                    self.log("사용자 중단.")
                    return STOPPED
                rc = self._buy(slot_no, slot_xy)
                if rc is not None:
                    return rc
                outcome, rc = self._modify_slot(slot_no, slot_xy)
                if outcome == _ABORT:
                    return rc
                if outcome == _BRICKED:
                    self.log(f"슬롯 {slot_no} 아이템 막힘 → 판매 후 재구매")
                    rc = self._sell(slot_no, slot_xy)
                    if rc is not None:
                        return rc
                    continue
                self.log(f"✅ 슬롯 {slot_no} 완료 — Lv.{self._target_level} 달성")
                break

        self.log(f"🎉 목표 {target_count}칸 모두 완료.")
        self._alert()
        return OK

    # ---- 골드 -----------------------------------------------------------
    def _gold_ok(self, img=None) -> bool:
        min_gold = self._safety.get("min_gold", 0)
        if not min_gold:
            return True
        img = img if img is not None else self.adb.screencap()
        gold = parse_gold(self.ocr.text_dump(img, self._safety.get("gold_region")))
        if gold is not None and gold < min_gold:
            self.log(f"골드 {gold:,} < 최소 {min_gold:,}. 중단.")
            return False
        return True

    # ---- 구매 / 판매 --------------------------------------------------
    def _buy(self, slot_no: int, slot_xy) -> int | None:
        cfg = self.cfg
        self.log(f"슬롯 {slot_no}: 아이템 구매")
        run_steps(self.adb, self.ocr, cfg.get("buy_sequence", []),
                  self._timing, self._stop, {"slot_xy": slot_xy}, self.log)
        img = self.adb.screencap()
        bad = self.ocr.find_any(img, cfg.get("buy_fail_keywords", []))
        if bad:
            _, ln = bad
            self.log(f"구매 실패 ('{ln.text}'). 중단.")
            return LOW_GOLD
        if not self._gold_ok(img):
            return LOW_GOLD
        return None

    def _sell(self, slot_no: int, slot_xy) -> int | None:
        self.log(f"슬롯 {slot_no}: 아이템 판매")
        run_steps(self.adb, self.ocr, self.cfg.get("sell_sequence", []),
                  self._timing, self._stop, {"slot_xy": slot_xy}, self.log)
        return None

    # ---- 레벨 ---------------------------------------------------------
    def _read_level(self, img) -> int | None:
        if not self._level_region:
            return None
        return parse_level(self.ocr.text_dump(img, self._level_region), self._level_pattern)

    # ---- 한 슬롯 개조 -----------------------------------------------
    def _modify_slot(self, slot_no: int, slot_xy) -> tuple[str, int | None]:
        cfg = self.cfg
        timing = self._timing
        rcfg = self._rcfg
        region = rcfg.get("region")
        consec = 0
        succ = 0
        dialog_fails = 0
        cur_level: int | None = self._read_level(self.adb.screencap())
        base = cur_level if cur_level is not None else self._start_level

        def level_now() -> int:
            return cur_level if cur_level is not None else base + succ

        def st() -> str:
            return (f"슬롯 {slot_no}  Lv.{level_now()}→{self._target_level}  "
                    f"연속실패 {consec}/{self._fail_limit}")

        if level_now() >= self._target_level:
            return _DONE, None

        while True:
            if self._stop():
                self.log("사용자 중단.")
                return _ABORT, STOPPED
            self._attempt += 1
            if self._attempt > self._max:
                self.log(f"최대 시도({self._max}) 도달. 중단.")
                return _ABORT, MAX_REACHED
            self._progress(self._attempt, self._max, st())
            self.log(f"--- 시도 {self._attempt}  ({st()}) ---")

            if not self._gold_ok():
                return _ABORT, LOW_GOLD

            try:
                run_steps(self.adb, self.ocr, cfg["attempt_sequence"],
                          timing, self._stop, {"slot_xy": slot_xy}, self.log)
            except SequenceError as e:
                # 개조 다이얼로그가 안 떴다 = 이번 개조는 실행 안 됨. 결과 읽지 말고 재시도.
                self._attempt -= 1
                dialog_fails += 1
                if dialog_fails >= 5:
                    self.log(f"⚠ 개조 다이얼로그가 5회 연속 안 뜸. 중단. ({e})")
                    return _ABORT, UNKNOWN
                self.log(f"⚠ {e} → 개조 미실행, 재시도 ({dialog_fails}/5)")
                self.adb.wait(1.0)
                continue
            dialog_fails = 0
            self.adb.wait(timing.get("after_modify", 1.8))

            import cv2
            img = self.adb.screencap()
            path = self._shots / f"{self._attempt:04d}.png"
            cv2.imwrite(str(path), img)
            if self._on_shot:
                self._on_shot(img, f"슬롯{slot_no} 시도{self._attempt}")

            hit = self.ocr.find_any(img, rcfg["success_keywords"], region)
            if hit:
                _, ln = hit
                succ += 1
                consec = 0
                lv = self._read_level(img)
                cur_level = lv if lv is not None else (cur_level + 1 if cur_level is not None else None)
                self.log(f"✅ 성공 ('{ln.text}')  Lv.{level_now()}")
                run_steps(self.adb, self.ocr, self._succ_seq, timing, self._stop, log=self.log)
                if level_now() >= self._target_level:
                    self.log(f"목표 레벨 Lv.{self._target_level} 도달.")
                    return _DONE, None
                self.adb.wait(timing.get("loop_idle", 0.4))
                continue

            locked = self.ocr.find_any(img, self._locked_kw, region) if self._locked_kw else None
            fail = self.ocr.find_any(img, rcfg["fail_keywords"], region)

            if fail and not locked:
                _, ln = fail
                consec += 1
                self.log(f"❌ 실패 ('{ln.text}')  연속 {consec}/{self._fail_limit}")
                run_steps(self.adb, self.ocr, self._fail_seq, timing, self._stop, log=self.log)

            if locked or (fail and consec >= self._fail_limit):
                why = "개조 불가 감지" if locked else f"연속 {self._fail_limit}회 실패"
                self.log(f"🔧 {why} → 슬롯 아이템 판매 대상")
                if locked:
                    run_steps(self.adb, self.ocr, self._fail_seq, timing, self._stop, log=self.log)
                return _BRICKED, None

            if fail:
                self.adb.wait(timing.get("loop_idle", 0.4))
                continue

            self.log("⚠ 결과 텍스트 인식 실패.")
            self.log(self.ocr.text_dump(img, region) or "  (인식된 텍스트 없음)")
            if self._safety.get("stop_on_unknown_screen", True):
                self.log(f"중단. 스크린샷: {path}")
                return _ABORT, UNKNOWN
            run_steps(self.adb, self.ocr, self._fail_seq, timing, self._stop, log=self.log)

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
