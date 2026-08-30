"""아스트로엔 개조 매크로 - GUI 설정/실행 도구.

    python -m src.gui

왼쪽 스크린샷에서 클릭 → 좌표, 드래그 → 영역을 잡아
개조 시퀀스/결과 판정 영역에 바로 적용하고, 그 자리에서 매크로를 실행한다.
"""
from __future__ import annotations

import copy
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from .adb import Adb
from .macro import Macro
from .ocr import Ocr
from .runner import config_path, load_config, save_config
from .stats import Stats

CAP_W, CAP_H = 960, 540
DISP_W = 720  # 캔버스 표시 폭


# ─────────────────────────────────────────────────────────────────────────────
# 스텝 편집 다이얼로그
# ─────────────────────────────────────────────────────────────────────────────
STEP_TYPES = ["tap", "tap_slot", "swipe", "wait", "wait_text", "key", "tap_if_text"]


class StepDialog(tk.Toplevel):
    def __init__(self, master, step: dict | None, picked_xy, picked_region):
        super().__init__(master)
        self.title("스텝 편집")
        self.resizable(False, False)
        self.result: dict | None = None
        self.transient(master)
        self.grab_set()

        step = step or {"tap": [picked_xy[0] if picked_xy else 0,
                               picked_xy[1] if picked_xy else 0]}
        self._type = tk.StringVar(value=next(t for t in STEP_TYPES if t in step))
        self._vars: dict[str, tk.StringVar] = {}

        top = ttk.Frame(self, padding=10)
        top.pack(fill="both", expand=True)
        ttk.Label(top, text="종류").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(top, values=STEP_TYPES, textvariable=self._type,
                          state="readonly", width=14)
        cb.grid(row=0, column=1, sticky="w", pady=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._render())

        self._body = ttk.Frame(top)
        self._body.grid(row=1, column=0, columnspan=3, sticky="we", pady=6)

        hint = []
        if picked_xy:
            hint.append(f"선택 좌표 {picked_xy}")
        if picked_region:
            hint.append(f"선택 영역 {picked_region}")
        self._picked_xy = picked_xy
        self._picked_region = picked_region
        if hint:
            ttk.Label(top, text=" / ".join(hint), foreground="#0a7").grid(
                row=2, column=0, columnspan=3, sticky="w")

        btns = ttk.Frame(top)
        btns.grid(row=3, column=0, columnspan=3, pady=(10, 0), sticky="e")
        ttk.Button(btns, text="확인", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="left")

        self._initial = step
        self._render()

    def _field(self, parent, label, key, default, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6))
        v = tk.StringVar(value=str(default))
        self._vars[key] = v
        ttk.Entry(parent, textvariable=v, width=10).grid(row=row, column=1, sticky="w", pady=2)

    def _render(self):
        for w in self._body.winfo_children():
            w.destroy()
        self._vars.clear()
        t = self._type.get()
        s = self._initial if t in self._initial else {}
        px = self._picked_xy or (0, 0)
        pr = self._picked_region or (0, 0, 0, 0)

        if t == "tap":
            xy = s.get("tap", list(px))
            self._field(self._body, "x", "x", xy[0], 0)
            self._field(self._body, "y", "y", xy[1], 1)
        elif t == "tap_slot":
            ttk.Label(self._body, text="현재 슬롯 아이템 좌표를 탭 (인자 없음)").grid(row=0, column=0)
        elif t == "swipe":
            v = s.get("swipe", [pr[0], pr[1], pr[2], pr[3], 400])
            for i, name in enumerate(["x1", "y1", "x2", "y2", "ms"]):
                self._field(self._body, name, name, v[i] if i < len(v) else (400 if name == "ms" else 0), i)
        elif t == "wait":
            self._field(self._body, "초", "sec", s.get("wait", 1.0), 0)
        elif t == "wait_text":
            d = s.get("wait_text", {})
            self._field(self._body, "텍스트", "text", d.get("text", "취소"), 0)
            self._field(self._body, "타임아웃(초)", "timeout", d.get("timeout", 3.0), 1)
            self._req = tk.BooleanVar(value=bool(d.get("required")))
            ttk.Checkbutton(self._body, text="required (없으면 시퀀스 중단/재시도)",
                            variable=self._req).grid(row=2, column=0, columnspan=2, sticky="w")
            reg = d.get("region", list(pr))
            for i, name in enumerate(["wx1", "wy1", "wx2", "wy2"]):
                self._field(self._body, ["x1", "y1", "x2", "y2"][i], name,
                            reg[i] if i < len(reg) else 0, i + 3)
        elif t == "key":
            self._field(self._body, "키코드", "code", s.get("key", 4), 0)
            ttk.Label(self._body, text="(4=뒤로가기)").grid(row=0, column=2, sticky="w")
        elif t == "tap_if_text":
            d = s.get("tap_if_text", {})
            self._field(self._body, "텍스트", "text", d.get("text", "확인"), 0)
            reg = d.get("region", list(pr))
            for i, name in enumerate(["rx1", "ry1", "rx2", "ry2"]):
                self._field(self._body, ["x1", "y1", "x2", "y2"][i], name,
                            reg[i] if i < len(reg) else 0, i + 1)

    def _ok(self):
        t = self._type.get()
        g = lambda k: self._vars[k].get().strip()
        try:
            if t == "tap":
                self.result = {"tap": [int(float(g("x"))), int(float(g("y")))]}
            elif t == "tap_slot":
                self.result = {"tap_slot": True}
            elif t == "swipe":
                self.result = {"swipe": [int(float(g(k))) for k in ("x1", "y1", "x2", "y2", "ms")]}
            elif t == "wait":
                self.result = {"wait": float(g("sec"))}
            elif t == "wait_text":
                wt = {
                    "text": g("text"),
                    "timeout": float(g("timeout")),
                    "region": [int(float(g(k))) for k in ("wx1", "wy1", "wx2", "wy2")],
                }
                if getattr(self, "_req", None) and self._req.get():
                    wt["required"] = True
                self.result = {"wait_text": wt}
            elif t == "key":
                self.result = {"key": int(g("code"))}
            elif t == "tap_if_text":
                self.result = {"tap_if_text": {
                    "text": g("text"),
                    "region": [int(float(g(k))) for k in ("rx1", "ry1", "rx2", "ry2")],
                }}
        except ValueError:
            messagebox.showerror("입력 오류", "숫자 칸에 올바른 값을 넣어주세요.", parent=self)
            return
        self.destroy()


def step_label(step: dict) -> str:
    if "tap_slot" in step:
        return "tap_slot  (현재 슬롯 아이템)"
    if "tap" in step:
        return f"tap  ({step['tap'][0]}, {step['tap'][1]})"
    if "swipe" in step:
        v = step["swipe"]
        return f"swipe ({v[0]},{v[1]}) → ({v[2]},{v[3]})  {v[4] if len(v) > 4 else 300}ms"
    if "wait_text" in step:
        d = step["wait_text"]
        req = " [required]" if d.get("required") else ""
        return f"wait_text  '{d.get('text')}'  {d.get('timeout', 3)}s{req}  {d.get('region')}"
    if "wait" in step:
        return f"wait  {step['wait']}s"
    if "key" in step:
        return f"key   {step['key']}"
    if "tap_if_text" in step:
        d = step["tap_if_text"]
        return f"tap_if_text  '{d.get('text')}'  {d.get('region')}"
    return str(step)


# ─────────────────────────────────────────────────────────────────────────────
# 시퀀스 편집 위젯 (재사용)
# ─────────────────────────────────────────────────────────────────────────────
class SequenceEditor(ttk.Frame):
    def __init__(self, master, app: "App", get_steps, set_steps):
        super().__init__(master)
        self.app = app
        self._get = get_steps
        self._set = set_steps

        self.lb = tk.Listbox(self, height=8, activestyle="dotbox")
        self.lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(self, command=self.lb.yview)
        sb.pack(side="left", fill="y")
        self.lb.config(yscrollcommand=sb.set)

        col = ttk.Frame(self)
        col.pack(side="left", fill="y", padx=6)
        for txt, cmd in [
            ("추가", self.add), ("편집", self.edit), ("삭제", self.remove),
            ("▲", lambda: self.move(-1)), ("▼", lambda: self.move(1)),
            ("＋선택좌표 tap", self.add_picked_tap),
        ]:
            ttk.Button(col, text=txt, width=13, command=cmd).pack(pady=2)
        self.refresh()

    def refresh(self):
        self.lb.delete(0, "end")
        for s in self._get():
            self.lb.insert("end", step_label(s))

    def _sel(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def add(self):
        d = StepDialog(self, None, self.app.picked_xy, self.app.picked_region)
        self.wait_window(d)
        if d.result:
            steps = self._get(); steps.append(d.result); self._set(steps); self.refresh()

    def add_picked_tap(self):
        if not self.app.picked_xy:
            messagebox.showinfo("안내", "먼저 왼쪽 화면에서 좌표를 클릭하세요.", parent=self)
            return
        steps = self._get()
        steps.append({"tap": list(self.app.picked_xy)})
        self._set(steps); self.refresh()

    def edit(self):
        i = self._sel()
        if i is None:
            return
        d = StepDialog(self, self._get()[i], self.app.picked_xy, self.app.picked_region)
        self.wait_window(d)
        if d.result:
            steps = self._get(); steps[i] = d.result; self._set(steps); self.refresh()
            self.lb.selection_set(i)

    def remove(self):
        i = self._sel()
        if i is None:
            return
        steps = self._get(); del steps[i]; self._set(steps); self.refresh()

    def move(self, delta):
        i = self._sel()
        if i is None:
            return
        j = i + delta
        steps = self._get()
        if 0 <= j < len(steps):
            steps[i], steps[j] = steps[j], steps[i]
            self._set(steps); self.refresh(); self.lb.selection_set(j)


# ─────────────────────────────────────────────────────────────────────────────
# 메인 앱
# ─────────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("아스트로엔 개조 매크로")
        self.geometry("1180x680")

        self.cfg = load_config()
        self.scale = DISP_W / CAP_W
        self.picked_xy: tuple[int, int] | None = None
        self.picked_region: tuple[int, int, int, int] | None = None
        self._img_bgr: np.ndarray | None = None
        self._tkimg = None
        self._drag_start = None

        self.adb: Adb | None = None
        self.ocr: Ocr | None = None
        self.macro_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.q: queue.Queue = queue.Queue()
        self.stats = Stats()
        self._ocr_lock = threading.Lock()
        self._shared_ocr_engine: Ocr | None = None

        self._build()
        self._refresh_stats()
        self.after(100, self._pump)

    # ---- 레이아웃 ---------------------------------------------------------
    def _build(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill="x")
        self.v_adb = tk.StringVar(value=self.cfg["adb"].get("path", ""))
        self.v_inst = tk.StringVar(value=self.cfg["adb"].get("instance_name", ""))
        self.v_serial = tk.StringVar(value=self.cfg["adb"].get("serial", ""))
        ttk.Label(bar, text="ADB").pack(side="left")
        ttk.Entry(bar, textvariable=self.v_adb, width=40).pack(side="left", padx=3)
        ttk.Button(bar, text="…", width=3, command=self._browse_adb).pack(side="left")
        ttk.Label(bar, text="  인스턴스명").pack(side="left")
        ttk.Entry(bar, textvariable=self.v_inst, width=10).pack(side="left", padx=3)
        ttk.Label(bar, text="serial").pack(side="left")
        ttk.Entry(bar, textvariable=self.v_serial, width=18).pack(side="left", padx=3)
        ttk.Button(bar, text="연결/새로고침", command=self.connect_refresh).pack(side="left", padx=6)
        self.v_status = tk.StringVar(value="미연결")
        ttk.Label(bar, textvariable=self.v_status, foreground="#c33").pack(side="left")

        body = ttk.Frame(self, padding=6)
        body.pack(fill="both", expand=True)

        # 왼쪽: 캔버스
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.canvas = tk.Canvas(left, width=DISP_W, height=int(CAP_H * self.scale),
                                bg="#222", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)

        pick = ttk.Frame(left, padding=(0, 6))
        pick.pack(fill="x")
        self.v_pick = tk.StringVar(value="클릭=좌표, 드래그=영역")
        ttk.Label(pick, textvariable=self.v_pick, font=("", 10, "bold")).pack(side="left")
        ttk.Button(pick, text="화면만 새로고침", command=lambda: self._grab_async()).pack(side="right")

        applyf = ttk.LabelFrame(left, text="선택한 좌표/영역 적용", padding=6)
        applyf.pack(fill="x")
        self.v_target = tk.StringVar(value="결과 판정 영역(region)")
        ttk.Combobox(applyf, textvariable=self.v_target, state="readonly",
                     values=["결과 판정 영역(region)", "골드 인식 영역(gold_region)",
                             "레벨 인식 영역(level_region)"]).pack(fill="x")
        ttk.Button(applyf, text="선택 영역을 여기에 적용", command=self._apply_region).pack(fill="x", pady=3)

        # 오른쪽: 탭
        nb = ttk.Notebook(body)
        nb.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._tab_slots(nb)
        self._tab_sequence(nb)
        self._tab_result(nb)
        self._tab_modify(nb)
        self._tab_safety(nb)
        self._tab_run(nb)
        self._tab_stats(nb)
        self._tab_ocr(nb)

        foot = ttk.Frame(self, padding=6)
        foot.pack(fill="x")
        ttk.Button(foot, text="설정 저장", command=self.save).pack(side="left")
        ttk.Button(foot, text="다시 불러오기", command=self.reload).pack(side="left", padx=4)
        ttk.Label(foot, text=str(config_path())).pack(side="right")

    # ---- 탭: 슬롯 / 구매 --------------------------------------------
    def _tab_slots(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="슬롯 / 구매")
        s = self.cfg.setdefault("slots", {})
        pos = s.get("positions", []) or []

        top = ttk.Frame(f)
        top.pack(anchor="w")
        ttk.Label(top, text="채울 슬롯 수").pack(side="left")
        self.v_slotcount = tk.StringVar(value=str(s.get("target_count", 8)))
        ttk.Spinbox(top, from_=1, to=8, width=4, textvariable=self.v_slotcount).pack(side="left", padx=4)
        ttk.Label(top, text="  아래에서 각 슬롯 아이템 좌표를 지정 (화면 클릭 후 '←' 버튼)").pack(side="left")

        grid = ttk.Frame(f)
        grid.pack(anchor="w", pady=6)
        self.v_slotpos = []
        for i in range(8):
            xy = pos[i] if i < len(pos) else [0, 0]
            v = tk.StringVar(value=f"{xy[0]}, {xy[1]}")
            self.v_slotpos.append(v)
            ttk.Label(grid, text=f"슬롯 {i+1}").grid(row=i, column=0, sticky="w", padx=(0, 6), pady=1)
            ttk.Entry(grid, textvariable=v, width=12).grid(row=i, column=1, pady=1)
            ttk.Button(grid, text="← 선택 좌표", width=11,
                       command=lambda vv=v: self._apply_xy(vv)).grid(row=i, column=2, padx=4)

        cols = ttk.Frame(f)
        cols.pack(fill="both", expand=True, pady=4)
        bl = ttk.LabelFrame(cols, text="구매: 구입탭 → 아이템 → wait_text(취소) → 구입버튼", padding=4)
        bl.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.buy_editor = SequenceEditor(
            bl, self, lambda: self.cfg.setdefault("buy_sequence", []),
            lambda v: self.cfg.__setitem__("buy_sequence", v))
        self.buy_editor.pack(fill="both", expand=True)
        sr = ttk.LabelFrame(cols, text="판매 시퀀스 (판매탭 → 슬롯 → 판매 → 확인)", padding=4)
        sr.pack(side="left", fill="both", expand=True)
        self.sell_editor = SequenceEditor(
            sr, self, lambda: self.cfg.setdefault("sell_sequence", []),
            lambda v: self.cfg.__setitem__("sell_sequence", v))
        self.sell_editor.pack(fill="both", expand=True)

        ttk.Label(f, text="구매 실패 키워드 (골드/가방 부족 등 — 보이면 중단)").pack(anchor="w", pady=(6, 0))
        self.t_buyfail = tk.Text(f, height=2, width=50)
        self.t_buyfail.pack(fill="x")
        self.t_buyfail.insert("1.0", "\n".join(self.cfg.get("buy_fail_keywords", [])))

    # ---- 탭: 시퀀스 -----------------------------------------------------
    def _tab_sequence(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="개조 시퀀스")

        e1 = ttk.LabelFrame(f, text="진입 시퀀스 — 슬롯 진입/판매 후 1회 ('개조' 탭 클릭 → 커서 개조 상태)",
                            padding=4)
        e1.pack(fill="both", expand=True, pady=(0, 6))
        self.enter_editor = SequenceEditor(
            e1, self,
            lambda: self.cfg.setdefault("modify_enter_sequence", []),
            lambda v: self.cfg.__setitem__("modify_enter_sequence", v))
        self.enter_editor.pack(fill="both", expand=True)

        e2 = ttk.LabelFrame(f, text="개조 1회 시퀀스 — 성공/실패와 무관하게 반복 "
                                    "(tap_slot=아이템 클릭 → 다이얼로그 → [개조] 버튼)", padding=4)
        e2.pack(fill="both", expand=True)
        self.seq_editor = SequenceEditor(
            e2, self,
            lambda: self.cfg.setdefault("attempt_sequence", []),
            lambda v: self.cfg.__setitem__("attempt_sequence", v))
        self.seq_editor.pack(fill="both", expand=True)

    # ---- 탭: 결과 판정 -------------------------------------------------
    def _tab_result(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="결과 판정")
        r = self.cfg.setdefault("result", {})

        ttk.Label(f, text="성공 키워드 (줄바꿈으로 구분, 하나라도 보이면 성공→중단)").pack(anchor="w")
        self.t_success = tk.Text(f, height=3, width=50)
        self.t_success.pack(fill="x")
        self.t_success.insert("1.0", "\n".join(r.get("success_keywords", [])))

        ttk.Label(f, text="실패 키워드 (보이면 실패 카운트 +1, 팝업 닫고 재시도)").pack(anchor="w", pady=(8, 0))
        self.t_fail = tk.Text(f, height=2, width=50)
        self.t_fail.pack(fill="x")
        self.t_fail.insert("1.0", "\n".join(r.get("fail_keywords", [])))

        ttk.Label(f, text="개조 불가/막힘 키워드 (보이면 복구 시퀀스 실행)").pack(anchor="w", pady=(8, 0))
        self.t_locked = tk.Text(f, height=2, width=50)
        self.t_locked.pack(fill="x")
        self.t_locked.insert("1.0", "\n".join(r.get("locked_keywords", [])))

        rf = ttk.Frame(f)
        rf.pack(fill="x", pady=8)
        ttk.Label(rf, text="결과 메시지 영역 [x1,y1,x2,y2]").pack(side="left")
        self.v_result_region = tk.StringVar(value=str(r.get("region", [300, 230, 660, 320])))
        ttk.Entry(rf, textvariable=self.v_result_region, width=24).pack(side="left", padx=4)
        ttk.Button(rf, text="← 선택 영역", command=lambda: self._apply_to(self.v_result_region)).pack(side="left")

        rt = ttk.Frame(f)
        rt.pack(fill="x")
        ttk.Label(rt, text="결과 확인 횟수 (말풍선 타이핑 연출 대비 폴링)").pack(side="left")
        self.v_result_tries = tk.StringVar(value=str(r.get("result_tries", 6)))
        ttk.Entry(rt, textvariable=self.v_result_tries, width=5).pack(side="left", padx=4)

        cols = ttk.Frame(f)
        cols.pack(fill="both", expand=True, pady=4)
        lc = ttk.LabelFrame(cols, text="성공 팝업 닫기 시퀀스", padding=4)
        lc.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.success_editor = SequenceEditor(
            lc, self,
            lambda: self.cfg["result"].setdefault("success_sequence", []),
            lambda v: self.cfg["result"].__setitem__("success_sequence", v))
        self.success_editor.pack(fill="both", expand=True)
        rc = ttk.LabelFrame(cols, text="실패 팝업 닫기 시퀀스", padding=4)
        rc.pack(side="left", fill="both", expand=True)
        self.fail_editor = SequenceEditor(
            rc, self,
            lambda: self.cfg["result"].setdefault("fail_sequence", []),
            lambda v: self.cfg["result"].__setitem__("fail_sequence", v))
        self.fail_editor.pack(fill="both", expand=True)

    # ---- 탭: 개조 규칙 ---------------------------------------------
    def _tab_modify(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="개조 규칙")
        m = self.cfg.setdefault("modify", {})

        ttk.Label(f, text="아이템 레벨 1~8. 개조 성공 1회 = 레벨 +1. 슬롯 아이템을 목표 레벨까지 올리면 "
                          "다음 슬롯으로. 연속 실패 한계 도달 시 그 아이템은 판매 후 재구매.",
                  wraplength=440, foreground="#555").pack(anchor="w")
        grid = ttk.Frame(f)
        grid.pack(anchor="w", pady=6)
        self.v_faillimit = tk.StringVar(value=str(m.get("fail_limit", 3)))
        self.v_targetlevel = tk.StringVar(value=str(m.get("target_level", 8)))
        self.v_startlevel = tk.StringVar(value=str(m.get("start_level", 1)))
        self.v_levelregion = tk.StringVar(value=str(m.get("level_region", [0, 0, 0, 0])))
        self.v_levelpattern = tk.StringVar(value=str(m.get("level_pattern", r"(?:lv|레벨)\s*[.:]?\s*([1-8])")))
        rows = [
            ("목표 레벨 (슬롯당, 1~8)", self.v_targetlevel, 6),
            ("시작 레벨 (새 아이템 기준)", self.v_startlevel, 6),
            ("연속 실패 한계 (도달 시 판매)", self.v_faillimit, 6),
            ("레벨 추출 정규식 (그룹1=숫자)", self.v_levelpattern, 28),
        ]
        for i, (lab, var, w) in enumerate(rows):
            ttk.Label(grid, text=lab).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Entry(grid, textvariable=var, width=w).grid(row=i, column=1, sticky="w")
        ttk.Label(grid, text="아이템 레벨(Lv.N) 표시 영역").grid(row=len(rows), column=0, sticky="w", padx=(0, 8), pady=2)
        lrf = ttk.Frame(grid)
        lrf.grid(row=len(rows), column=1, sticky="w")
        ttk.Entry(lrf, textvariable=self.v_levelregion, width=20).pack(side="left")
        ttk.Button(lrf, text="← 선택 영역", command=lambda: self._apply_to(self.v_levelregion)).pack(side="left")

    # ---- 탭: 안전장치/타이밍 -----------------------------------------
    def _tab_safety(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="안전장치 / 타이밍")
        s = self.cfg.setdefault("safety", {})
        t = self.cfg.setdefault("timing", {})

        self.v_max = tk.StringVar(value=str(s.get("max_attempts", 100)))
        self.v_mingold = tk.StringVar(value=str(s.get("min_gold", 0)))
        self.v_goldregion = tk.StringVar(value=str(s.get("gold_region", [515, 478, 645, 500])))
        self.v_stopunknown = tk.BooleanVar(value=bool(s.get("stop_on_unknown_screen", True)))
        self.v_saveshots = tk.StringVar(value=str(s.get("save_shots", "events")))

        grid = ttk.Frame(f)
        grid.pack(anchor="w")
        rows = [
            ("최대 시도 횟수", self.v_max),
            ("최소 골드 (0=검사안함)", self.v_mingold),
            ("골드 인식 영역", self.v_goldregion),
        ]
        for i, (lab, var) in enumerate(rows):
            ttk.Label(grid, text=lab).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 8))
            ttk.Entry(grid, textvariable=var, width=24).grid(row=i, column=1, sticky="w")
        ttk.Checkbutton(f, text="성공/실패 문구를 못 읽으면 중단 (오작동 방지, 권장)",
                        variable=self.v_stopunknown).pack(anchor="w", pady=6)
        ssf = ttk.Frame(f)
        ssf.pack(anchor="w")
        ttk.Label(ssf, text="스크린샷 저장").pack(side="left", padx=(0, 8))
        ttk.Combobox(ssf, textvariable=self.v_saveshots, state="readonly", width=10,
                     values=["all", "events", "none"]).pack(side="left")
        ttk.Label(ssf, text="  all=매 시도  events=문제 상황만  none=저장 안 함",
                  foreground="#777").pack(side="left")

        ttk.Separator(f).pack(fill="x", pady=8)
        ttk.Label(f, text="대기 시간(초)", font=("", 10, "bold")).pack(anchor="w")
        self.timing_vars = {}
        tg = ttk.Frame(f)
        tg.pack(anchor="w")
        defs = [("after_tap", 0.6), ("after_modify", 1.8), ("after_confirm", 1.2),
                ("loop_idle", 0.4), ("poll_interval", 0.5)]
        for i, (k, dv) in enumerate(defs):
            ttk.Label(tg, text=k).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 8))
            v = tk.StringVar(value=str(t.get(k, dv)))
            self.timing_vars[k] = v
            ttk.Entry(tg, textvariable=v, width=8).grid(row=i, column=1, sticky="w")

    # ---- 탭: 실행 -----------------------------------------------------
    def _tab_run(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="실행")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.v_dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="DRY-RUN (입력 안 보냄)", variable=self.v_dry).pack(side="left")
        ttk.Label(top, text="  최대 횟수 override").pack(side="left")
        self.v_runmax = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.v_runmax, width=6).pack(side="left", padx=3)
        ttk.Button(top, text="전체 시작", command=self.start_all).pack(side="left", padx=(12, 2))
        ttk.Button(top, text="전체 정지", command=self.stop_all).pack(side="left")

        ins = ttk.LabelFrame(f, text="인스턴스", padding=6)
        ins.pack(fill="x", pady=(6, 2))
        row = ttk.Frame(ins)
        row.pack(fill="x")
        ttk.Button(row, text="🔍 인스턴스 조회", command=self._scan_instances).pack(side="left")
        d = self.cfg.get("display", {})
        ttk.Button(row, text=f"📐 해상도 {d.get('width', 960)}×{d.get('height', 540)} 맞추기",
                   command=self._enforce_resolution).pack(side="left", padx=6)
        self.v_scanmsg = tk.StringVar(value="조회를 누르면 BlueStacks 인스턴스 목록을 불러옵니다.")
        ttk.Label(row, textvariable=self.v_scanmsg, foreground="#777").pack(side="left", padx=8)
        ttk.Button(row, text="선택 적용", command=self._apply_checked_instances).pack(side="right")

        self.inst_box = ttk.Frame(ins)
        self.inst_box.pack(fill="x", pady=(4, 0))
        self.inst_checks: dict[str, tk.BooleanVar] = {}
        self._inst_serials: dict[str, str] = {}
        self.v_instances = tk.StringVar(value=", ".join(self._instance_names()))

        self.run_nb = ttk.Notebook(f)
        self.run_nb.pack(fill="both", expand=True, pady=4)
        self._viewed_name = None
        self.run_nb.bind("<<NotebookTabChanged>>",
                         lambda e: setattr(self, "_viewed_name", self._current_run_name()))
        self.runners: dict[str, dict] = {}
        self._rebuild_runners()

        self.v_stattotal = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_stattotal, foreground="#777").pack(anchor="w")

    # ---- 멀티 인스턴스 실행 ------------------------------------------
    def _scan_instances(self):
        conf = self.cfg.get("adb", {}).get("bluestacks_conf", "")
        exe = self.v_adb.get().strip() or self.cfg.get("adb", {}).get("path", "")
        self.v_scanmsg.set("조회 중...")
        self.update_idletasks()

        def work():
            try:
                from .adb import list_instances
                items = list_instances(exe, conf, probe=True)
                self.q.put(("insts", None, items))
            except Exception as e:
                self.q.put(("insts", None, e))

        threading.Thread(target=work, daemon=True).start()

    def _populate_instances(self, items):
        for w in self.inst_box.winfo_children():
            w.destroy()
        self.inst_checks = {}
        self._inst_serials = {}
        if isinstance(items, Exception):
            self.v_scanmsg.set(f"조회 실패: {items}")
            return
        running = [str(x) for x in self.cfg.get("instances", [])]
        on = sum(1 for it in items if it["online"])
        self.v_scanmsg.set(f"{len(items)}개 (온라인 {on}) — 실행할 인스턴스를 체크하세요")
        for i, it in enumerate(items):
            name = it["name"]
            self._inst_serials[name] = it["serial"] or ""
            checked = it["online"] and (not running or name in running)
            v = tk.BooleanVar(value=checked)
            self.inst_checks[name] = v
            mark = "●" if it["online"] else "○"
            color = "#3a3" if it["online"] else "#999"
            tgt = f"{self.cfg.get('display', {}).get('width', 960)}x{self.cfg.get('display', {}).get('height', 540)}"
            sz = it.get("size")
            szlabel = f"  [{sz}]" if sz else ""
            szcolor = "#3a3" if (sz == tgt or not sz) else "#c33"
            fr = ttk.Frame(self.inst_box)
            fr.grid(row=i // 3, column=i % 3, sticky="w", padx=6, pady=1)
            ttk.Checkbutton(fr, text=f"{name}", variable=v).pack(side="left")
            tk.Label(fr, text=f"{mark} {it['serial'] or '-'}", fg=color).pack(side="left")
            if szlabel:
                tk.Label(fr, text=szlabel, fg=szcolor).pack(side="left")

    def _apply_checked_instances(self):
        if self.inst_checks:
            names = [n for n, v in self.inst_checks.items() if v.get()]
            if not names:
                messagebox.showinfo("안내", "체크된 인스턴스가 없습니다.", parent=self)
                return
            self.v_instances.set(", ".join(names))
        self._rebuild_runners()

    def _enforce_resolution(self):
        names = [n for n, v in self.inst_checks.items() if v.get()] if self.inst_checks \
            else list(self.runners)
        if not names:
            messagebox.showinfo("안내", "먼저 인스턴스를 조회/체크하세요.", parent=self)
            return
        d = self.cfg.get("display", {})
        w, h, dens = int(d.get("width", 960)), int(d.get("height", 540)), d.get("density", 160)
        exe = self.v_adb.get().strip() or self.cfg["adb"]["path"]
        self.v_scanmsg.set("해상도 적용 중...")
        self.update_idletasks()

        def work():
            lines = []
            for n in names:
                serial = self._inst_serials.get(n, "")
                try:
                    cfg = {"adb": {"path": exe, "serial": serial, "instance_name": n,
                                   "bluestacks_conf": self.cfg["adb"].get("bluestacks_conf")}}
                    ad = Adb(cfg)
                    ad.connect()
                    before = ad.get_size()
                    ad.set_size(w, h, dens)
                    lines.append(f"{n}: {before} → {w}x{h}")
                except Exception as e:
                    lines.append(f"{n}: 실패 ({e})")
            self.q.put(("resmsg", None, "\n".join(lines)))

        threading.Thread(target=work, daemon=True).start()

    def _instance_names(self) -> list[str]:
        ins = self.cfg.get("instances")
        if ins:
            return [str(x).strip() for x in ins if str(x).strip()]
        a = self.cfg.get("adb", {})
        return [a.get("instance_name") or a.get("serial") or "default"]

    def _current_run_name(self):
        try:
            return self.run_nb.tab(self.run_nb.select(), "text")
        except Exception:
            return None

    def _rebuild_runners(self):
        for st in self.runners.values():
            st["stop_event"].set()
        for t in list(self.run_nb.tabs()):
            self.run_nb.forget(t)
        self.runners = {}
        names = [x.strip() for x in self.v_instances.get().replace("\n", ",").split(",") if x.strip()]
        if not names:
            names = ["default"]
        self.cfg["instances"] = names
        for name in names:
            fr = ttk.Frame(self.run_nb, padding=6)
            self.run_nb.add(fr, text=name)
            bar = ttk.Frame(fr)
            bar.pack(fill="x")
            sv = tk.StringVar(value="대기 중")
            ttk.Label(bar, textvariable=sv, font=("", 10, "bold")).pack(side="left")
            b2 = ttk.Button(bar, text="정지", state="disabled",
                            command=lambda n=name: self.stop_instance(n))
            b2.pack(side="right")
            b1 = ttk.Button(bar, text="시작", command=lambda n=name: self.start_instance(n))
            b1.pack(side="right", padx=4)
            lg = tk.Text(fr, height=13, bg="#111", fg="#ddd", insertbackground="#ddd")
            lg.pack(fill="both", expand=True, pady=4)
            lg.config(state="disabled")
            self.runners[name] = dict(status=sv, log=lg, start=b1, stop=b2,
                                      stop_event=threading.Event(), thread=None)
        self._viewed_name = self._current_run_name()

    def start_instance(self, name: str):
        st = self.runners.get(name)
        if not st or (st["thread"] and st["thread"].is_alive()):
            return
        try:
            self._collect()
        except Exception as e:
            messagebox.showerror("설정 오류", str(e))
            return
        cfg = copy.deepcopy(self.cfg)
        cfg.setdefault("adb", {})
        cfg["adb"]["instance_name"] = name
        cfg["adb"]["serial"] = self._inst_serials.get(name, "")
        dry = self.v_dry.get()
        mx = int(self.v_runmax.get()) if self.v_runmax.get().strip() else None
        st["stop_event"].clear()
        st["start"].config(state="disabled")
        st["stop"].config(state="normal")
        self._log_line("=" * 30, name)

        def work():
            try:
                shared = None if dry else self._shared_ocr()
                m = Macro(
                    cfg, dry_run=dry,
                    on_log=lambda s: self.q.put(("log", name, s)),
                    should_stop=st["stop_event"].is_set,
                    on_progress=lambda a, b, c: self.q.put(("prog", name, f"{c}  {a}/{b}")),
                    on_shot=lambda img, tag: (self.q.put(("img", name, img))
                                              if name == self._viewed_name else None),
                    stats=None if dry else self.stats,
                    instance_label=name,
                    on_stat=lambda: self.q.put(("stat", None, None)),
                    ocr=shared,
                )
                code = m.run(mx)
                self.q.put(("log", name, f"종료 코드 {code}"))
            except Exception as e:
                self.q.put(("log", name, f"오류: {e}"))
            finally:
                self.q.put(("done", name, None))

        st["thread"] = threading.Thread(target=work, daemon=True)
        st["thread"].start()

    def stop_instance(self, name: str):
        st = self.runners.get(name)
        if st:
            st["stop_event"].set()
            self._log_line("정지 요청됨...", name)

    def start_all(self):
        for n in list(self.runners):
            self.start_instance(n)

    def stop_all(self):
        for n in list(self.runners):
            self.stop_instance(n)

    # ---- 탭: 통계 -------------------------------------------------
    def _tab_stats(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="통계")

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="레벨별 개조 통계 (from_level 기준, 전체 누적)",
                  font=("", 10, "bold")).pack(side="left")
        ttk.Button(top, text="새로고침", command=self._refresh_stats).pack(side="right")
        ttk.Button(top, text="초기화", command=self._reset_stats).pack(side="right", padx=4)

        cols = ("level", "attempts", "success", "fail", "unknown", "rate", "bricks")
        heads = ("레벨", "시도", "성공", "실패", "미인식", "성공률", "막힘(3연속실패)")
        self.stat_tree = ttk.Treeview(f, columns=cols, show="headings", height=9)
        for c, h in zip(cols, heads):
            self.stat_tree.heading(c, text=h)
            self.stat_tree.column(c, width=90 if c in ("level", "rate", "bricks") else 70,
                                  anchor="center")
        self.stat_tree.column("bricks", width=120)
        self.stat_tree.pack(fill="x", pady=6)
        self.stat_tree.tag_configure("brick", background="#5a1e1e", foreground="#fff")

        ttk.Label(f, text="레벨별 막힘 횟수", font=("", 9, "bold")).pack(anchor="w", pady=(8, 2))
        self.brick_canvas = tk.Canvas(f, height=140, bg="#1a1a1a", highlightthickness=0)
        self.brick_canvas.pack(fill="x")
        self.v_statfoot = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_statfoot, foreground="#777").pack(anchor="w", pady=4)

    def _refresh_stats(self):
        if not hasattr(self, "stat_tree"):
            return
        max_lv = int(self.cfg.get("modify", {}).get("target_level", 8))
        summ = self.stats.summary(max_level=max(max_lv, 8))
        self.stat_tree.delete(*self.stat_tree.get_children())
        for lv, r in summ.items():
            rate = f"{r['rate']*100:.1f}%" if (r["success"] + r["fail"]) else "-"
            tag = ("brick",) if r["bricks"] else ()
            self.stat_tree.insert("", "end", tags=tag, values=(
                f"Lv.{lv}→{lv+1}", r["attempts"], r["success"], r["fail"],
                r["unknown"], rate, r["bricks"]))
        self._draw_brick_chart(summ)
        t = self.stats.totals()
        msg = (f"전체: 시도 {t['attempts']} · 성공 {t['success']} "
               f"({t['rate']*100:.1f}%) · 막힘 {t['bricks']}")
        self.v_statfoot.set(msg)
        if hasattr(self, "v_stattotal"):
            self.v_stattotal.set("📊 " + msg)

    def _draw_brick_chart(self, summ: dict):
        c = self.brick_canvas
        c.delete("all")
        c.update_idletasks()
        w = c.winfo_width() or 600
        h = 140
        levels = list(summ.keys())
        if not levels:
            return
        maxb = max((summ[l]["bricks"] for l in levels), default=0) or 1
        n = len(levels)
        bw = w / n
        for i, lv in enumerate(levels):
            b = summ[lv]["bricks"]
            bh = (b / maxb) * (h - 30)
            x0 = i * bw + bw * 0.2
            x1 = i * bw + bw * 0.8
            c.create_rectangle(x0, h - 20 - bh, x1, h - 20, fill="#c0453e", outline="")
            c.create_text((x0 + x1) / 2, h - 10, text=f"Lv.{lv}", fill="#aaa", font=("", 8))
            if b:
                c.create_text((x0 + x1) / 2, h - 26 - bh, text=str(b), fill="#fff", font=("", 8))

    def _reset_stats(self):
        if messagebox.askyesno("초기화", "통계 DB를 모두 지웁니다. 계속?", parent=self):
            self.stats.reset()
            self._refresh_stats()

    # ---- 탭: OCR 테스트 ---------------------------------------------
    def _tab_ocr(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="OCR 테스트")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="현재 화면 OCR", command=self._ocr_full).pack(side="left")
        ttk.Button(top, text="선택 영역만 OCR", command=self._ocr_region).pack(side="left", padx=4)
        self.ocr_out = tk.Text(f, height=20, bg="#111", fg="#9f9")
        self.ocr_out.pack(fill="both", expand=True, pady=6)

    # ---- ADB / 화면 -------------------------------------------------
    def _browse_adb(self):
        p = filedialog.askopenfilename(title="HD-Adb.exe 선택",
                                       filetypes=[("exe", "*.exe"), ("all", "*.*")])
        if p:
            self.v_adb.set(p)

    def _sync_adb_cfg(self):
        self.cfg["adb"]["path"] = self.v_adb.get().strip()
        self.cfg["adb"]["instance_name"] = self.v_inst.get().strip()
        self.cfg["adb"]["serial"] = self.v_serial.get().strip()

    def connect_refresh(self):
        self._sync_adb_cfg()
        # 실행 탭에서 인스턴스를 골라놨으면 그 인스턴스 화면을 본다 (캘리브레이션/미리보기)
        viewed = self._current_run_name()
        cfg = copy.deepcopy(self.cfg)
        if viewed and viewed != "default":
            cfg["adb"]["instance_name"] = viewed
            cfg["adb"]["serial"] = self._inst_serials.get(viewed, "")
        try:
            self.adb = Adb(cfg)
            self.adb.connect()
            self.v_status.set(f"연결됨 {self.adb.serial}"
                              + (f" ({viewed})" if viewed and viewed != 'default' else ""))
            self._grab_async()
        except Exception as e:
            self.v_status.set("연결 실패")
            messagebox.showerror("연결 실패", str(e))

    def _grab_async(self):
        if not self.adb:
            return
        threading.Thread(target=self._grab_worker, daemon=True).start()

    def _grab_worker(self):
        try:
            img = self.adb.screencap()
            self.q.put(("img", None, img))
        except Exception as e:
            self.q.put(("log", None, f"스크린샷 실패: {e}"))

    def _show_img(self, bgr):
        self._img_bgr = bgr
        disp = cv2.resize(bgr, (DISP_W, int(bgr.shape[0] * self.scale)))
        rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        self._tkimg = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tkimg)
        self._redraw_pick()

    def _redraw_pick(self):
        self.canvas.delete("pick")
        s = self.scale
        if self.picked_region:
            x1, y1, x2, y2 = self.picked_region
            self.canvas.create_rectangle(x1 * s, y1 * s, x2 * s, y2 * s,
                                         outline="#0f0", width=2, tags="pick")
        if self.picked_xy:
            x, y = self.picked_xy
            self.canvas.create_line(x * s - 8, y * s, x * s + 8, y * s, fill="#ff0", width=2, tags="pick")
            self.canvas.create_line(x * s, y * s - 8, x * s, y * s + 8, fill="#ff0", width=2, tags="pick")

    # ---- 캔버스 이벤트 --------------------------------------------
    def _to_cap(self, ev):
        return int(ev.x / self.scale), int(ev.y / self.scale)

    def _on_hover(self, ev):
        cx, cy = self._to_cap(ev)
        self.v_pick.set(f"커서 ({cx}, {cy})  |  " + self._pick_text())

    def _pick_text(self):
        parts = []
        if self.picked_xy:
            parts.append(f"좌표 {self.picked_xy}")
        if self.picked_region:
            parts.append(f"영역 {list(self.picked_region)}")
        return "  ".join(parts) if parts else "선택 없음"

    def _on_press(self, ev):
        self._drag_start = self._to_cap(ev)

    def _on_drag(self, ev):
        if not self._drag_start:
            return
        x1, y1 = self._drag_start
        x2, y2 = self._to_cap(ev)
        self.picked_region = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self._redraw_pick()
        self.v_pick.set(self._pick_text())

    def _on_release(self, ev):
        x2, y2 = self._to_cap(ev)
        x1, y1 = self._drag_start or (x2, y2)
        if abs(x2 - x1) < 4 and abs(y2 - y1) < 4:
            self.picked_xy = (x2, y2)          # 클릭 = 좌표
        else:
            self.picked_region = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self._redraw_pick()
        self.v_pick.set(self._pick_text())

    def _apply_region(self):
        if not self.picked_region:
            messagebox.showinfo("안내", "먼저 화면에서 영역을 드래그하세요.", parent=self)
            return
        reg = list(self.picked_region)
        tgt = self.v_target.get()
        if tgt.startswith("결과"):
            self.v_result_region.set(str(reg))
        elif tgt.startswith("레벨"):
            self.v_levelregion.set(str(reg))
        else:
            self.v_goldregion.set(str(reg))

    def _apply_to(self, var: tk.StringVar):
        if not self.picked_region:
            messagebox.showinfo("안내", "먼저 화면에서 영역을 드래그하세요.", parent=self)
            return
        var.set(str(list(self.picked_region)))

    def _apply_xy(self, var: tk.StringVar):
        if not self.picked_xy:
            messagebox.showinfo("안내", "먼저 왼쪽 화면에서 좌표를 클릭하세요.", parent=self)
            return
        var.set(f"{self.picked_xy[0]}, {self.picked_xy[1]}")

    # ---- OCR 테스트 ---------------------------------------------
    def _shared_ocr(self) -> Ocr:
        """모든 인스턴스가 공유하는 OCR 엔진 (CPU 과점유 방지)."""
        with self._ocr_lock:
            if self._shared_ocr_engine is None:
                self._shared_ocr_engine = Ocr(self.cfg)
            return self._shared_ocr_engine

    def _ensure_ocr(self):
        self.ocr = self._shared_ocr()
        return self.ocr

    def _ocr_full(self):
        self._run_ocr(None)

    def _ocr_region(self):
        if not self.picked_region:
            messagebox.showinfo("안내", "먼저 화면에서 영역을 드래그하세요.", parent=self)
            return
        self._run_ocr(list(self.picked_region))

    def _run_ocr(self, region):
        if self._img_bgr is None:
            messagebox.showinfo("안내", "먼저 연결/새로고침으로 화면을 가져오세요.", parent=self)
            return
        self.ocr_out.delete("1.0", "end")
        self.ocr_out.insert("end", "OCR 실행 중...\n")
        self.update_idletasks()

        def work():
            try:
                dump = self._ensure_ocr().text_dump(self._img_bgr, region)
                self.q.put(("ocr", dump or "(인식된 텍스트 없음)"))
            except Exception as e:
                self.q.put(("ocr", f"오류: {e}"))
        threading.Thread(target=work, daemon=True).start()

    # ---- 설정 저장/로드 -----------------------------------------
    def _collect(self):
        self._sync_adb_cfg()
        r = self.cfg.setdefault("result", {})
        lines = lambda t: [x.strip() for x in t.get("1.0", "end").splitlines() if x.strip()]
        r["success_keywords"] = lines(self.t_success)
        r["fail_keywords"] = lines(self.t_fail)
        r["locked_keywords"] = lines(self.t_locked)
        r["region"] = _parse_list(self.v_result_region.get())
        r["result_tries"] = int(self.v_result_tries.get())
        for k in ("dismiss_sequence",):
            r.pop(k, None)

        m = self.cfg.setdefault("modify", {})
        m["fail_limit"] = int(self.v_faillimit.get())
        m["target_level"] = int(self.v_targetlevel.get())
        m["start_level"] = int(self.v_startlevel.get())
        m["level_region"] = _parse_list(self.v_levelregion.get())
        m["level_pattern"] = self.v_levelpattern.get().strip()
        for k in ("target_successes", "recovery_sequence"):
            m.pop(k, None)

        sl = self.cfg.setdefault("slots", {})
        sl["target_count"] = int(self.v_slotcount.get())
        sl["positions"] = [_parse_list(v.get())[:2] or [0, 0] for v in self.v_slotpos]
        self.cfg["buy_fail_keywords"] = lines(self.t_buyfail)

        if hasattr(self, "v_instances"):
            names = [x.strip() for x in self.v_instances.get().replace("\n", ",").split(",") if x.strip()]
            self.cfg["instances"] = names or self._instance_names()

        s = self.cfg.setdefault("safety", {})
        s["max_attempts"] = int(self.v_max.get())
        s["min_gold"] = int(self.v_mingold.get())
        s["gold_region"] = _parse_list(self.v_goldregion.get())
        s["stop_on_unknown_screen"] = bool(self.v_stopunknown.get())
        s["save_shots"] = self.v_saveshots.get()

        t = self.cfg.setdefault("timing", {})
        for k, v in self.timing_vars.items():
            t[k] = float(v.get())

    def save(self):
        try:
            self._collect()
            p = save_config(self.cfg)
            self._log_line(f"설정 저장됨: {p}")
            messagebox.showinfo("저장", f"저장 완료\n{p}")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    def reload(self):
        self.cfg = load_config()
        messagebox.showinfo("다시 불러오기", "config.yaml 을 다시 불러왔습니다. 창을 다시 열면 반영됩니다.")

    # ---- 큐 펌프 ----------------------------------------------
    def _pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if len(msg) == 3:
                    _, who, payload = msg
                else:
                    who, payload = None, (msg[1] if len(msg) > 1 else None)
                if kind == "log":
                    self._log_line(payload, who)
                elif kind == "img":
                    if who is None or who == self._current_run_name():
                        self._show_img(payload)
                elif kind == "prog":
                    st = self.runners.get(who)
                    if st:
                        st["status"].set(payload)
                elif kind == "ocr":
                    self.ocr_out.delete("1.0", "end")
                    self.ocr_out.insert("end", payload)
                elif kind == "insts":
                    self._populate_instances(payload)
                elif kind == "resmsg":
                    self.v_scanmsg.set("해상도 적용 완료")
                    messagebox.showinfo("해상도 맞추기", payload, parent=self)
                elif kind == "stat":
                    self._stat_dirty = True
                elif kind == "done":
                    st = self.runners.get(who)
                    if st:
                        st["start"].config(state="normal")
                        st["stop"].config(state="disabled")
                        st["status"].set("대기 중")
                    self._stat_dirty = True
        except queue.Empty:
            pass
        if getattr(self, "_stat_dirty", False):
            now = time.monotonic()
            if now - getattr(self, "_stat_last", 0.0) > 1.5:   # 통계 갱신 최대 1.5초마다
                self._stat_dirty = False
                self._stat_last = now
                self._refresh_stats()
        self.after(120, self._pump)

    def _log_line(self, s: str, who: str | None = None):
        w = None
        if who and who in self.runners:
            w = self.runners[who]["log"]
        elif self.runners:
            w = self.runners.get(self._current_run_name(), {}).get("log")
        if w is None:
            print(s, flush=True)
            return
        w.config(state="normal")
        w.insert("end", s + "\n")
        w.see("end")
        w.config(state="disabled")


def _parse_list(s: str) -> list[int]:
    return [int(float(x)) for x in s.strip().strip("[]()").split(",") if x.strip()]


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
