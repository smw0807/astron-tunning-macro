"""단발 입력 테스트.

    python -m tools.tap 202 128            # 탭
    python -m tools.tap swipe 486 354 480 300 400   # 스와이프(x1 y1 x2 y2 ms)
    python -m tools.tap seq                # config 의 attempt_sequence 1회 실행
"""
from __future__ import annotations

import sys

from src.adb import Adb
from src.ocr import Ocr
from src.runner import load_config, run_steps


def main():
    cfg = load_config()
    adb = Adb(cfg)
    adb.connect()
    a = sys.argv[1:]

    if not a:
        print(__doc__)
        return
    if a[0] == "swipe":
        x1, y1, x2, y2 = map(int, a[1:5])
        ms = int(a[5]) if len(a) > 5 else 300
        adb.swipe(x1, y1, x2, y2, ms)
        print(f"swipe {x1},{y1} -> {x2},{y2} ({ms}ms)")
    elif a[0] == "seq":
        ocr = Ocr(cfg)
        run_steps(adb, ocr, cfg["attempt_sequence"], cfg["timing"])
        print("attempt_sequence 1회 실행 완료")
    else:
        x, y = int(a[0]), int(a[1])
        adb.tap(x, y)
        print(f"tap {x},{y}")


if __name__ == "__main__":
    main()
