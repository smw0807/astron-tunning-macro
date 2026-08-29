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
        self._a = a
        self.exe = a["path"]
        self.package = a.get("package")
        self.serial = a.get("serial") or self._resolve_serial(a) or self._only_local_device()
        if not self.serial:
            raise RuntimeError("adb serial 을 확인할 수 없습니다. config.yaml 의 adb.serial 을 직접 지정하세요.")

    # ---- 연결 ----------------------------------------------------------------
    def _resolve_serial(self, a: dict) -> str | None:
        """bluestacks.conf 에서 instance_name(display_name) 의 adb_port 를 찾는다."""
        name = a.get("instance_name")
        conf = a.get("bluestacks_conf")
        if not name or not conf or not Path(conf).exists():
            return None
        text = Path(conf).read_text(encoding="utf-8", errors="ignore")
        m = re.search(rf'bst\.instance\.([^.]+)\.display_name="{re.escape(name)}"', text)
        if not m:
            return None
        key = m.group(1)
        m2 = re.search(rf'bst\.instance\.{re.escape(key)}\.status\.adb_port="(\d+)"', text)
        if not m2:
            return None
        return f"127.0.0.1:{m2.group(1)}"

    def _list_online(self) -> list[str]:
        out = self._run(["devices"], device=False)
        found = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "device":
                found.append(parts[0])
        return found

    def _only_local_device(self) -> str | None:
        """이미 붙어있는 127.0.0.1 기기가 하나뿐이면 그걸 쓴다."""
        local = [d for d in self._list_online() if d.startswith("127.0.0.1:")]
        return local[0] if len(local) == 1 else None

    def _is_online(self, serial: str) -> bool:
        return serial in self._list_online()

    def connect(self) -> None:
        self._run(["connect", self.serial], device=False)
        if self._is_online(self.serial):
            return
        # 포트가 바뀐 경우(블루스택 재시작 등) 재탐색
        alt = self._resolve_serial(self._a) or self._only_local_device()
        if alt and alt != self.serial:
            self._run(["connect", alt], device=False)
            if self._is_online(alt):
                self.serial = alt
                return
        raise RuntimeError(
            f"기기 연결 실패: {self.serial}\n"
            f"현재 온라인: {self._list_online() or '(없음)'}\n"
            f"BlueStacks 가 켜져 있는지, config.yaml 의 adb.instance_name 이 맞는지 확인하세요."
        )

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
