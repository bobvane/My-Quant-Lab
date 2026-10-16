#!/usr/bin/env python3
"""为 Lab 浏览器竞态冒烟测试播种两条「已完成」的 AI 研究任务。

设计要点（与 backend/tests/conftest.py 同源，离线、无网络、无生产数据）：

* 在 import app 之前设置环境变量，确保引擎指向我们指定的文件型 SQLite。
* 用 Base.metadata.create_all 在一个文件 SQLite 上建表，使「播种进程」
  与「紧随其后启动的 uvicorn worker」使用同一份 schema、同一批行（不要用
  :memory:，否则两个进程各持一份，uvicorn 读不到种子数据）。
* 只插入两条当前仓库 LabView.vue 竞态守卫需要的最小字段，不触碰任何生产库、
  不写入任何真实数据、不引入任何新增依赖。

用法:
    python scripts/seed_lab_race_runs.py

可通过环境变量 DATABASE_URL 覆盖数据库位置（默认 backend/.ci-race.sqlite3）。
若 DATABASE_URL 恰好指向本脚本自有的临时库（backend/.ci-race.sqlite3），
重跑时会先删除旧文件，以规避 app 的 after_create 触发器在已存在库上抛
"trigger already exists"（CI 每次都是全新文件，本地重跑才需要这层保护）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# --- 在 import app 之前设置环境（必须最先执行） ---
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

_db_default = "sqlite+pysqlite:///" + str(BACKEND / ".ci-race.sqlite3")
os.environ.setdefault("DATABASE_URL", _db_default)
os.environ.setdefault("MARKET_DATA_PROVIDER", "synthetic")
os.environ.setdefault("SECRET_KEY", "ci-race-secret-key-0123456789")
os.environ.setdefault("APP_ENVIRONMENT", "development")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("CELERY_BROKER_URL", "")
os.environ.setdefault("CELERY_RESULT_BACKEND", "")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

sys.path.insert(0, str(BACKEND))

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.domain.models import AIResearchRun  # noqa: E402


def _scratch_db_path() -> "str | None":
    """仅当 DATABASE_URL 指向本脚本自有临时库时才返回其路径，否则返回 None。

    避免误删用户显式指定的任何真实数据库。
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("sqlite+pysqlite:///"):
        return None
    path = url[len("sqlite+pysqlite:///"):]
    if os.path.basename(path) != ".ci-race.sqlite3":
        return None
    return path


def main() -> int:
    # 若指向本脚本自有的临时库，重跑前先删除，规避 app 的 after_create 触发器
    # 在已存在库上抛 "trigger already exists"（CI 每次都是全新文件，不需要此步）。
    scratch = _scratch_db_path()
    if scratch and os.path.exists(scratch):
        os.remove(scratch)

    # 幂等：create_all 只补齐缺失的表，重复运行安全。
    Base.metadata.create_all(engine)

    rows = [
        AIResearchRun(
            id=1,
            question="为什么动量因子在 2023 年的小盘股上失效，回测该如何校正样本偏差？",
            status="completed",
            current_step="done",
            attempts=0,
        ),
        AIResearchRun(
            id=2,
            question="如何用 Walk-Forward 验证均线交叉策略是否过拟合样本内？",
            status="completed",
            current_step="done",
            attempts=0,
        ),
    ]

    with SessionLocal() as db:
        added = 0
        for row in rows:
            if db.get(AIResearchRun, row.id) is None:
                db.add(row)
                added += 1
        db.commit()

    print(
        f"seeded Lab race runs: total={len(rows)} added={added} "
        f"db={os.environ['DATABASE_URL']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())