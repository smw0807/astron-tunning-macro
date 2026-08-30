"""개조 통계 - SQLite 누적. 레벨별 시도/성공/실패/막힘.

레벨은 '시도 전 아이템 레벨'(from_level) 기준.
  from_level=3, result='success' → Lv.3→4 개조 성공 1건
막힘(brick) = 연속 실패 한계 도달로 그 아이템을 판매한 사건. 막힌 레벨 기록.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .runner import ROOT

RESULTS = ("success", "fail", "unknown")


class Stats:
    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path) if db_path else ROOT / "stats.db"
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                ts TEXT, instance TEXT, slot INTEGER,
                from_level INTEGER, result TEXT
            );
            CREATE TABLE IF NOT EXISTS bricks (
                ts TEXT, instance TEXT, slot INTEGER, level INTEGER
            );
            """
        )
        self.db.commit()

    # ---- 기록 ---------------------------------------------------------
    def record_attempt(self, instance: str, slot: int, from_level: int | None, result: str) -> None:
        if result not in RESULTS:
            result = "unknown"
        with self._lock:
            self.db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), instance, slot, from_level, result),
            )
            self.db.commit()

    def record_brick(self, instance: str, slot: int, level: int | None) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO bricks VALUES (?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), instance, slot, level),
            )
            self.db.commit()

    # ---- 조회 ---------------------------------------------------------
    def summary(self, max_level: int = 8, instance: str | None = None) -> dict[int, dict]:
        """{level: {attempts, success, fail, unknown, bricks, rate}}  (level = from_level)"""
        rows = {
            lv: dict(attempts=0, success=0, fail=0, unknown=0, bricks=0, rate=0.0)
            for lv in range(1, max_level)
        }
        where = "WHERE instance = ?" if instance else ""
        args = (instance,) if instance else ()
        with self._lock:
            for lv, res, n in self.db.execute(
                f"SELECT from_level, result, COUNT(*) FROM attempts {where} GROUP BY from_level, result",
                args,
            ):
                if lv in rows:
                    rows[lv][res if res in RESULTS else "unknown"] += n
                    rows[lv]["attempts"] += n
            for lv, n in self.db.execute(
                f"SELECT level, COUNT(*) FROM bricks {where} GROUP BY level", args
            ):
                if lv in rows:
                    rows[lv]["bricks"] = n
        for r in rows.values():
            done = r["success"] + r["fail"]
            r["rate"] = (r["success"] / done) if done else 0.0
        return rows

    def totals(self, instance: str | None = None) -> dict:
        s = self.summary(instance=instance)
        att = sum(r["attempts"] for r in s.values())
        suc = sum(r["success"] for r in s.values())
        brk = sum(r["bricks"] for r in s.values())
        return dict(attempts=att, success=suc, bricks=brk,
                    rate=(suc / att) if att else 0.0)

    def reset(self) -> None:
        with self._lock:
            self.db.executescript("DELETE FROM attempts; DELETE FROM bricks;")
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()
