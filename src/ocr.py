"""RapidOCR(한국어) 래퍼 + 화면 텍스트 검색 헬퍼."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from rapidocr_onnxruntime import RapidOCR


@dataclass
class Line:
    text: str
    conf: float
    cx: float
    cy: float
    box: list


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


class Ocr:
    def __init__(self, cfg: dict):
        o = cfg.get("ocr", {})
        kwargs = {}
        if o.get("rec_model_path"):
            kwargs["rec_model_path"] = o["rec_model_path"]
        if o.get("rec_keys_path"):
            kwargs["rec_keys_path"] = o["rec_keys_path"]
        if o.get("rec_img_shape"):
            kwargs["rec_img_shape"] = list(o["rec_img_shape"])
        # CPU 전부 물고 늘어져서 PC 렉 걸리는 것 방지 (기본 2스레드)
        nthreads = int(o.get("max_threads", 2))
        for k in ("intra_op_num_threads", "inter_op_num_threads"):
            kwargs[k] = nthreads
        try:
            self.engine = RapidOCR(**kwargs)
        except TypeError:
            for k in ("intra_op_num_threads", "inter_op_num_threads"):
                kwargs.pop(k, None)
            self.engine = RapidOCR(**kwargs)
        # 여러 인스턴스가 한 엔진을 공유할 때 직렬화 (CPU 과점유 방지 + 스레드 안전)
        self._lock = threading.Lock()

    def read(self, img: np.ndarray, region: list | None = None) -> list[Line]:
        ox, oy = 0, 0
        crop = img
        if region:
            x1, y1, x2, y2 = region
            ox, oy = x1, y1
            crop = img[y1:y2, x1:x2]
        with self._lock:
            res, _ = self.engine(crop)
        lines: list[Line] = []
        for box, text, conf in (res or []):
            cx = ox + sum(p[0] for p in box) / 4
            cy = oy + sum(p[1] for p in box) / 4
            lines.append(Line(text, float(conf), cx, cy, box))
        return lines

    @staticmethod
    def match(lines: list[Line], keywords: list[str]):
        """이미 읽은 lines 에서 키워드 매칭 (OCR 재실행 없음)."""
        for kw in keywords or ():
            key = _norm(kw)
            for ln in lines:
                if key in _norm(ln.text):
                    return kw, ln
        return None

    def find(self, img: np.ndarray, keyword: str, region: list | None = None) -> Line | None:
        m = self.match(self.read(img, region), [keyword])
        return m[1] if m else None

    def find_any(self, img: np.ndarray, keywords: list[str], region: list | None = None):
        return self.match(self.read(img, region), keywords)

    def text_dump(self, img: np.ndarray, region: list | None = None) -> str:
        return "\n".join(
            f"({int(l.cx):>4},{int(l.cy):>4}) {l.conf:.2f}  {l.text}"
            for l in self.read(img, region)
        )
