"""SQLite 数据层：单文件库，兼容原 projects.json 的 dict-of-project 结构。

设计要点：
- 整个项目（含 shots/characters/log 等）序列化为 JSON 存一行，键为项目 id。
  这样所有业务代码无需感知 SQL，_load_projects/_save_projects 语义与 JSON 版完全一致。
- WAL 模式 + busy_timeout：读并发不阻塞，短写互不覆盖。
- 事务内读-改-写（save 单项目时先 SELECT 再 UPDATE），避免并发写整表覆盖。
"""
import json
import os
import sqlite3
import threading
import time as _time

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(_BASE_DIR, "data", "drama.db")

_lock = threading.Lock()  # 进程内写锁（asyncio 单线程，实际防的是多线程调用场景）


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_FILE, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                created_at TEXT,
                updated_at TEXT,
                status TEXT,
                data TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                pid TEXT NOT NULL,
                kind TEXT NOT NULL,          -- design / frames / videos / tts
                stage TEXT,                  -- 传给流水线的 stage 参数
                status TEXT NOT NULL,        -- queued / running / done / failed / canceled
                payload TEXT,                -- JSON 扩展参数（如角色名、镜头 id）
                progress TEXT,               -- JSON 进度详情（当前阶段、已完成子项）
                error TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_pid ON tasks(pid)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                pid TEXT NOT NULL,
                kind TEXT NOT NULL,          -- frames / designs / videos / audio / finals
                path TEXT NOT NULL,          -- 存储根下的相对路径
                size INTEGER DEFAULT 0,      -- 字节数
                meta TEXT,                   -- JSON（shot_id、角色名等）
                created_at TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_assets_pid ON assets(pid)")


def _row_to_proj(row) -> dict:
    return json.loads(row["data"])


def load_projects() -> dict:
    """返回 {pid: proj_dict}，与旧 JSON 版 _load_projects 完全同构。"""
    init_db()
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT data FROM projects").fetchall()
    return {json.loads(r["data"])["id"]: json.loads(r["data"]) for r in rows}


def save_project(proj: dict):
    """保存单个项目（upsert）。只动自己那一行，不覆盖其他项目。"""
    init_db()
    now = _now()
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO projects (id, created_at, updated_at, status, data)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 updated_at=excluded.updated_at,
                 status=excluded.status,
                 data=excluded.data""",
            (proj["id"], proj.get("created_at", now), now, proj.get("status", ""), 
             json.dumps(proj, ensure_ascii=False)),
        )


def delete_project(pid: str):
    init_db()
    with _lock, _conn() as c:
        c.execute("DELETE FROM projects WHERE id = ?", (pid,))
        c.execute("DELETE FROM tasks WHERE pid = ?", (pid,))
        c.execute("DELETE FROM assets WHERE pid = ?", (pid,))


# ---------------- 资产登记（媒体文件清单，供统计/清理） ----------------

def add_asset(a: dict):
    init_db()
    now = _now()
    with _lock, _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO assets (id, pid, kind, path, size, meta, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (a["id"], a["pid"], a["kind"], a["path"], a.get("size", 0),
             json.dumps(a.get("meta") or {}, ensure_ascii=False), now),
        )


def assets_for_pid(pid: str) -> list[dict]:
    init_db()
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM assets WHERE pid = ? ORDER BY created_at", (pid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.get("meta") or "{}")
        out.append(d)
    return out


def delete_assets_for_pid(pid: str):
    init_db()
    with _lock, _conn() as c:
        c.execute("DELETE FROM assets WHERE pid = ?", (pid,))


# ---------------- 任务表（队列化 + 断点续跑） ----------------

def create_task(tid: str, pid: str, kind: str, stage: str = "", payload: dict | None = None):
    init_db()
    now = _now()
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO tasks (id, pid, kind, stage, status, payload, progress, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'queued', ?, '{}', ?, ?)""",
            (tid, pid, kind, stage, json.dumps(payload or {}, ensure_ascii=False), now, now),
        )


def get_task(tid: str) -> dict | None:
    init_db()
    with _conn() as c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    return dict(r) if r else None


def update_task(tid: str, status: str | None = None, progress: dict | None = None, error: str | None = None):
    """部分更新任务状态。progress 传 dict 时与现有内容浅合并（断点信息累积）。"""
    init_db()
    now = _now()
    sets, vals = ["updated_at = ?"], [now]
    if status is not None:
        sets.append("status = ?"); vals.append(status)
    if error is not None:
        sets.append("error = ?"); vals.append(error)
    if progress is not None:
        with _conn() as c:
            c.row_factory = sqlite3.Row
            r = c.execute("SELECT progress FROM tasks WHERE id = ?", (tid,)).fetchone()
            cur = json.loads(r["progress"] or "{}") if r else {}
        cur.update(progress)
        sets.append("progress = ?"); vals.append(json.dumps(cur, ensure_ascii=False))
    vals.append(tid)
    with _lock, _conn() as c:
        c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals)


def finish_task(tid: str, status: str, error: str = ""):
    """终结任务：done / failed / canceled。"""
    update_task(tid, status=status, error=error or None)


def tasks_for_pid(pid: str) -> list[dict]:
    init_db()
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM tasks WHERE pid = ? ORDER BY created_at", (pid,)).fetchall()
    return [dict(r) for r in rows]


def unfinished_tasks() -> list[dict]:
    """服务重启后要恢复的任务：running（中断的）和 queued（还没跑的）。"""
    init_db()
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM tasks WHERE status IN ('running','queued') ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def _now() -> str:
    return _time.strftime("%Y-%m-%d %H:%M:%S")
