"""BlueStacks HD-Adb.exe 래퍼."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import numpy as np
import cv2


class Adb:
    def __init__(self, cfg: dict):
        a = cfg["adb"]
        self.exe = a["path"]
        self.package = a.get("package")
        self.serial = a.get("serial") or self._resolve_serial(a)
        if not self.serial:
            raise RuntimeError("adb serial 을 확인할 수 없습니다. config.yaml 의 adb.serial 을 직접 지정하세요.")

    # ---- 연결 ----------------------------------------------------------------
    def _resolve_serial(self, a: dict) -> str | None:
        name = a.get("instance_name")
        conf = a.get("bluestacks_conf")
        if not name or not conf or not Path(conf).exists():
            return None
        text = Path(conf).read_text(encoding="utf-8", errors="ignore")
        # display_name 이 name 인 인스턴스 키를 찾는다
        m = re.search(rf'bst\.instance\.([^.]+)\.display_name="{re.escape(name)}"', text)
        if not m:
            return None
        key = m.group(1)
        m2 = re.search(rf'bst\.instance\.{re.escape(key)}\.status\.adb_port="(\d+)"', text)
        if not m2:
            return None
        return f"127.0.0.1:{m2.group(1)}"

    def connect(self) -> None:
        self._run(["connect", self.serial], device=False)
        out = self._run(["devices"], device=False)
        if self.serial not in out or "device" not in out.split(self.serial)[-1][:20]:
            raise RuntimeError(f"기기 연결 실패: {self.serial}\n{out}")

    # ---- 저수준 실행 -------------------------------------------------------
    def _run(self, args: list[str], device: bool = True, binary: bool = False):
        cmd = [self.exe]
        if device:
            cmd += ["-s", self.serial]
        cmd += args
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        if binary:
            return r.stdout
        return (r.stdout or b"").decode("utf-8", errors="replace")

    def shell(self, cmd: str) -> str:
        return self._run(["shell", cmd])

    # ---- 입력 -------------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")

    def key(self, code: int) -> None:
        self.shell(f"input keyevent {int(code)}")

    # ---- 화면 -----------------------------------------------------------
    def screencap(self) -> np.ndarray:
        """BGR ndarray 반환."""
        raw = self._run(["exec-out", "screencap", "-p"], binary=True)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("스크린샷 디코드 실패 (기기 화면이 꺼져있거나 adb 오류)")
        return img

    def current_focus(self) -> str:
        return self.shell("dumpsys window | grep -E 'mCurrentFocus'")

    def is_game_foreground(self) -> bool:
        return bool(self.package) and self.package in self.current_focus()

    def wait(self, sec: float) -> None:
        time.sleep(sec)
