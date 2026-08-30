"""BlueStacks HD-Adb.exe 래퍼."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import numpy as np
import cv2


def list_online_serials(exe: str) -> list[str]:
    r = subprocess.run([exe, "devices"], capture_output=True, timeout=15)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            found.append(parts[0])
    return found


def list_instances(exe: str, conf_path: str, probe: bool = True) -> list[dict]:
    """bluestacks.conf 의 모든 인스턴스: [{key, name, serial, online}].

    probe=True 면 각 인스턴스에 adb connect 시도해서 online 여부 확인.
    """
    text = Path(conf_path).read_text(encoding="utf-8", errors="ignore")
    names = dict(re.findall(r'bst\.instance\.(\w+)\.display_name="([^"]*)"', text))
    ports = dict(re.findall(r'bst\.instance\.(\w+)\.status\.adb_port="(\d+)"', text))
    online = set(list_online_serials(exe))
    out = []
    for key, name in sorted(names.items()):
        port = ports.get(key)
        serial = f"127.0.0.1:{port}" if port else None
        is_on = serial in online if serial else False
        if probe and serial and not is_on:
            try:
                subprocess.run([exe, "connect", serial], capture_output=True, timeout=5)
            except Exception:
                pass
        out.append({"key": key, "name": name or key, "serial": serial, "online": is_on})
    if probe:
        online = set(list_online_serials(exe))
        for it in out:
            it["online"] = bool(it["serial"]) and it["serial"] in online
            it["size"] = None
            if it["online"]:
                try:
                    r = subprocess.run([exe, "-s", it["serial"], "shell", "wm size"],
                                       capture_output=True, timeout=8)
                    txt = (r.stdout or b"").decode("utf-8", errors="replace")
                    m = re.search(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", txt)
                    if m:
                        it["size"] = f"{m.group(1)}x{m.group(2)}"
                except Exception:
                    pass
    return out


class Adb:
    def __init__(self, cfg: dict):
        a = cfg["adb"]
        self._a = a
        self.exe = a["path"]
        self.package = a.get("package")
        self.serial = a.get("serial") or self._resolve_serial(a) or self._only_local_device()
        self._raw_ok = bool(a.get("raw_screencap", True))
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
        return list_online_serials(self.exe)

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

    # ---- 해상도 -------------------------------------------------------
    def get_size(self) -> tuple[int, int] | None:
        out = self.shell("wm size")
        m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(r"Physical size:\s*(\d+)x(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def get_density(self) -> int | None:
        out = self.shell("wm density")
        m = re.search(r"Override density:\s*(\d+)", out) or re.search(r"Physical density:\s*(\d+)", out)
        return int(m.group(1)) if m else None

    def set_size(self, w: int, h: int, density: int | None = None) -> None:
        self.shell(f"wm size {int(w)}x{int(h)}")
        if density:
            self.shell(f"wm density {int(density)}")

    def reset_size(self) -> None:
        self.shell("wm size reset")
        self.shell("wm density reset")

    # ---- 화면 -----------------------------------------------------------
    def screencap(self) -> np.ndarray:
        """BGR ndarray 반환. 원시(raw) 캡처 우선 — PNG 인코딩/디코딩 CPU 절약."""
        if self._raw_ok:
            try:
                return self._screencap_raw()
            except Exception:
                self._raw_ok = False  # 한 번 실패하면 PNG 로 고정
        raw = self._run(["exec-out", "screencap", "-p"], binary=True)
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("스크린샷 디코드 실패 (기기 화면이 꺼져있거나 adb 오류)")
        return img

    def _screencap_raw(self) -> np.ndarray:
        buf = self._run(["exec-out", "screencap"], binary=True)
        w = int.from_bytes(buf[0:4], "little")
        h = int.from_bytes(buf[4:8], "little")
        if not (0 < w <= 4096 and 0 < h <= 4096):
            raise ValueError(f"raw 캡처 크기 이상 (w={w} h={h})")
        body = w * h * 4
        header = len(buf) - body            # 실제 헤더 길이 역산 (12 또는 16)
        if header not in (12, 16):
            raise ValueError(f"raw 캡처 형식 불일치 (len={len(buf)} body={body})")
        px = np.frombuffer(buf[header:header + body], dtype=np.uint8).reshape(h, w, 4)
        return cv2.cvtColor(px, cv2.COLOR_RGBA2BGR)

    def current_focus(self) -> str:
        return self.shell("dumpsys window | grep -E 'mCurrentFocus'")

    def is_game_foreground(self) -> bool:
        return bool(self.package) and self.package in self.current_focus()

    def wait(self, sec: float) -> None:
        time.sleep(sec)
