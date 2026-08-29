"""좌표/OCR 캘리브레이션 도구.

    python -m tools.calibrate                 # 스크린샷 + 격자 + OCR 덤프
    python -m tools.calibrate --region 300 230 660 320   # 특정 영역만 OCR
    python -m tools.calibrate --watch         # 1초마다 갱신 (개조 결과 팝업 잡을 때)
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from src.adb import Adb
from src.ocr import Ocr
from src.runner import ROOT, load_config


def grid(img):
    g = img.copy()
    h, w = g.shape[:2]
    for x in range(0, w, 50):
        c = (0, 200, 255) if x % 100 == 0 else (0, 100, 120)
        cv2.line(g, (x, 0), (x, h), c, 1)
        if x % 100 == 0:
            cv2.putText(g, str(x), (x + 1, 12), cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 200, 255), 1)
    for y in range(0, h, 50):
        c = (0, 200, 255) if y % 100 == 0 else (0, 100, 120)
        cv2.line(g, (0, y), (w, y), c, 1)
        if y % 100 == 0:
            cv2.putText(g, str(y), (1, y + 11), cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 200, 255), 1)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--region", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    ap.add_argument("--watch", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    adb = Adb(cfg)
    adb.connect()
    ocr = Ocr(cfg)
    outdir = ROOT / "captures"
    outdir.mkdir(exist_ok=True)

    def once(i=0):
        img = adb.screencap()
        cv2.imwrite(str(outdir / "cal.png"), img)
        cv2.imwrite(str(outdir / "cal_grid.png"), grid(img))
        print(f"\n=== {img.shape[1]}x{img.shape[0]}  포커스: {adb.current_focus().strip()[-60:]}")
        print(f"저장: {outdir/'cal.png'}, {outdir/'cal_grid.png'}")
        print("--- OCR (중심좌표 신뢰도 텍스트) ---")
        print(ocr.text_dump(img, args.region))

    if args.watch:
        try:
            while True:
                once()
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    else:
        once()


if __name__ == "__main__":
    main()
