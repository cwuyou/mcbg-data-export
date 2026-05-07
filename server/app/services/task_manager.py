"""任务管理：在后台线程执行导出，通过 asyncio.Queue 推送进度给 SSE 订阅者。"""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Callable


def _resolve_export_dir() -> Path:
    """导出目录优先用环境变量 MCBG_EXPORT_DIR，便于避开系统 TEMP 所在盘。"""
    env = os.environ.get("MCBG_EXPORT_DIR")
    if env:
        p = Path(env).expanduser()
    else:
        p = Path(tempfile.gettempdir()) / "mcbg-export"
    p.mkdir(parents=True, exist_ok=True)
    return p


EXPORT_DIR = _resolve_export_dir()

TASK_TTL_SECONDS = 24 * 3600


@dataclass
class Task:
    task_id: str
    status: str = "pending"
    processed: int = 0
    total: Optional[int] = None
    message: Optional[str] = None
    file_path: Optional[str] = None
    filename: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    cancelled: threading.Event = field(default_factory=threading.Event)
    subscribers: List[asyncio.Queue] = field(default_factory=list)
    loop: Optional[asyncio.AbstractEventLoop] = None

    def snapshot(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "processed": self.processed,
            "total": self.total,
            "message": self.message,
            "filename": self.filename,
            "download_url": f"/api/download/{self.task_id}" if self.file_path else None,
        }


class TaskManager:
    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> Task:
        task = Task(task_id=uuid.uuid4().hex)
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        task.loop = loop
        with self._lock:
            self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def _broadcast(self, task: Task) -> None:
        if not task.loop:
            return
        snap = task.snapshot()
        for q in list(task.subscribers):
            try:
                asyncio.run_coroutine_threadsafe(q.put(snap), task.loop)
            except Exception:
                pass

    def update(self, task: Task, **fields) -> None:
        for k, v in fields.items():
            setattr(task, k, v)
        self._broadcast(task)

    async def subscribe(self, task: Task) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        task.subscribers.append(q)
        await q.put(task.snapshot())
        return q

    def unsubscribe(self, task: Task, q: asyncio.Queue) -> None:
        try:
            task.subscribers.remove(q)
        except ValueError:
            pass

    def run_in_thread(self, task: Task, job: Callable[[Task], None]) -> None:
        def target():
            try:
                self.update(task, status="running")
                job(task)
                if task.cancelled.is_set():
                    self.update(task, status="cancelled")
                else:
                    self.update(task, status="done")
            except Exception as e:
                self.update(task, status="failed", message=str(e))

        threading.Thread(target=target, name=f"export-{task.task_id}", daemon=True).start()

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task:
            return False
        task.cancelled.set()
        return True

    def gc(self) -> None:
        now = time.time()
        with self._lock:
            expired = [t for t in self._tasks.values() if now - t.created_at > TASK_TTL_SECONDS]
            for t in expired:
                if t.file_path and os.path.exists(t.file_path):
                    try:
                        os.remove(t.file_path)
                    except OSError:
                        pass
                self._tasks.pop(t.task_id, None)


task_manager = TaskManager()


def cleanup_stale_files(ttl_seconds: int = TASK_TTL_SECONDS) -> int:
    """启动时调用：清理 EXPORT_DIR 中超过 TTL 的残留文件。"""
    now = time.time()
    removed = 0
    for f in EXPORT_DIR.iterdir():
        try:
            if f.is_file() and now - f.stat().st_mtime > ttl_seconds:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def disk_free_bytes(path: Path) -> int:
    import shutil
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return -1


def export_filename(base: Optional[str], ext: str) -> str:
    safe = (base or "export").strip().replace("/", "_").replace("\\", "_")
    return f"{safe}.{ext}"


def make_output_path(task_id: str, filename: str) -> str:
    return str(EXPORT_DIR / f"{task_id}__{filename}")
