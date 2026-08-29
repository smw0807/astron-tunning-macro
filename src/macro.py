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
OK = 0            # 목표 성공 횟수 달성
MAX_REACHED = 1   # 최대 시도 도달
LOW_GOLD = 2      # 골드 부족
UNKNOWN = 3       # 결과 화면 인식 실패
STOPPED = 4       # 사용자 중단
LOCKED = 5        # 개조 불가 상태인데 복구 시퀀스가 없음


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
        timing = cfg["timing"]
        safety = cfg["safety"]
        rcfg = cfg["result"]
        total = max_attempts or safety.get("max_attempts", 100)

        if self.adb.package and not self.adb.is_game_foreground():
            self.log(f"경고: 게임({self.adb.package})이 포그라운드가 아닙니다.")

        mod = cfg.get("modify", {})
        fail_limit = int(mod.get("fail_limit", 3))
        target = int(mod.get("target_successes", 0))
        target_level = int(mod.get("target_level", 0))
        start_level = int(mod.get("start_level", 1))
        level_region = mod.get("level_region")
        if level_region and (len(level_region) != 4
                             or level_region[0] >= level_region[2]
                             or level_region[1] >= level_region[3]):
            level_region = None       # [0,0,0,0] 등 미설정으로 취급
        level_pattern = mod.get("level_pattern") or DEFAULT_LEVEL_PATTERN
        recovery = mod.get("recovery_sequence", [])
        # 하위호환: 예전 dismiss_sequence 를 fail_sequence 로 사용
        fail_seq = rcfg.get("fail_sequence", rcfg.get("dismiss_sequence", []))
        succ_seq = rcfg.get("success_sequence", [])
        locked_kw = rcfg.get("locked_keywords", [])

        shots = ROOT / "captures" / f"run_{datetime.now():%Y%m%d_%H%M%S}"
        shots.mkdir(parents=True, exist_ok=True)
        min_gold = safety.get("min_gold", 0)

        attempt = 0
        successes = 0
        consec_fails = 0
        cur_level: int | None = None

        def read_level(img) -> int | None:
            if not level_region:
                return None
            lv = parse_level(self.ocr.text_dump(img, level_region), level_pattern)
            return lv

        def est_level() -> int | None:
            if cur_level is not None:
                return cur_level
            return start_level + successes if start_level else None

        def status() -> str:
            parts = [f"성공 {successes}"]
            if target:
                parts[0] += f"/{target}"
            lv = est_level()
            if lv is not None:
                parts.append(f"Lv.{lv}" + (f"→{target_level}" if target_level else ""))
            parts.append(f"연속실패 {consec_fails}/{fail_limit}")
            return "  ".join(parts)

        def level_reached() -> bool:
            lv = est_level()
            return bool(target_level) and lv is not None and lv >= target_level

        # 시작 시 이미 목표 레벨이면 바로 종료
        if target_level:
            img0 = self.adb.screencap()
            cur_level = read_level(img0)
            if level_reached():
                self.log(f"이미 목표 레벨(Lv.{cur_level} ≥ {target_level}). 할 일 없음.")
                return OK

        while attempt < total:
            if self._stop():
                self.log("사용자 중단.")
                return STOPPED
            attempt += 1
            self._progress(attempt, total, status())
            self.log(f"--- 시도 {attempt}/{total}  ({status()}) ---")

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

            region = rcfg.get("region")
            hit = self.ocr.find_any(img, rcfg["success_keywords"], region)
            if hit:
                _, ln = hit
                successes += 1
                consec_fails = 0
                lv = read_level(img)
                if lv is not None:
                    cur_level = lv
                elif cur_level is not None:
                    cur_level += 1          # OCR 실패 시 +1 추정
                self.log(f"✅ 개조 성공! ('{ln.text}')  누적 성공 {successes}"
                         + (f"  Lv.{cur_level}" if cur_level is not None else ""))
                self._progress(attempt, total, status())
                run_steps(self.adb, self.ocr, succ_seq, timing, self._stop)
                if level_reached():
                    self.log(f"목표 레벨 Lv.{target_level} 도달. 중단.")
                    self._alert()
                    return OK
                if target and successes >= target:
                    self.log(f"목표 성공 {target}회 달성. 중단.")
                    self._alert()
                    return OK
                self.adb.wait(timing.get("loop_idle", 0.4))
                continue

            locked = self.ocr.find_any(img, locked_kw, region) if locked_kw else None
            fail = self.ocr.find_any(img, rcfg["fail_keywords"], region)

            if fail and not locked:
                _, ln = fail
                consec_fails += 1
                self.log(f"❌ 실패 ('{ln.text}')  연속 {consec_fails}/{fail_limit}")
                run_steps(self.adb, self.ocr, fail_seq, timing, self._stop)

            if locked or (fail and consec_fails >= fail_limit):
                why = "개조 불가 상태 감지" if locked else f"연속 {fail_limit}회 실패"
                if not recovery:
                    self.log(f"⚠ {why}. 복구 시퀀스가 없어 중단합니다. (GUI › 개조 규칙 › 복구 시퀀스)")
                    return LOCKED
                self.log(f"🔧 {why} → 복구 시퀀스 실행")
                run_steps(self.adb, self.ocr, recovery, timing, self._stop)
                consec_fails = 0
                self.adb.wait(timing.get("loop_idle", 0.4))
                continue

            if fail:
                self.adb.wait(timing.get("loop_idle", 0.4))
                continue

            self.log("⚠ 결과 텍스트를 인식하지 못했습니다.")
            self.log(self.ocr.text_dump(img, region) or "  (인식된 텍스트 없음)")
            if safety.get("stop_on_unknown_screen", True):
                self.log(f"중단. 스크린샷: {path}")
                return UNKNOWN
            run_steps(self.adb, self.ocr, fail_seq, timing, self._stop)

        self.log(f"최대 시도 횟수({total}) 도달. 누적 성공 {successes}. 중단.")
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
