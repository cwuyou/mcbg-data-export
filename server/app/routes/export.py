from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from ..models import ExportRequest, ExportTaskResponse
from ..services.odps_client import credentials_from_headers, build_odps, OdpsCredentials
from ..services.reader import open_table_reader, open_sql_reader
from ..services.exporter_csv import stream_csv
from ..services.exporter_xlsx import write_xlsx
from ..services.task_manager import (
    task_manager,
    export_filename,
    make_output_path,
)


router = APIRouter()


def _build_sql_for_table(req: ExportRequest) -> str:
    cols = ", ".join(req.columns) if req.columns else "*"
    parts = [f"SELECT {cols} FROM {req.table}"]
    if req.partition:
        parts.append(f"WHERE {req.partition.replace(',', ' AND ').replace('/', ' AND ')}")
        if req.where:
            parts.append(f"AND ({req.where})")
    elif req.where:
        parts.append(f"WHERE {req.where}")
    return " ".join(parts)


def _open_reader(odps, req: ExportRequest):
    if req.sql:
        return open_sql_reader(odps, req.sql)
    if not req.table:
        raise HTTPException(status_code=400, detail="either `sql` or `table` is required")
    if req.where:
        return open_sql_reader(odps, _build_sql_for_table(req))
    return open_table_reader(odps, req.table, req.partition, req.columns)


def _content_disposition(filename: str) -> str:
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


@router.post("/api/export/stream")
def export_stream(
    req: ExportRequest,
    creds: OdpsCredentials = Depends(credentials_from_headers),
):
    """CSV 专用：零磁盘，直接流式返回给浏览器。"""
    if req.format != "csv":
        raise HTTPException(status_code=400, detail="streaming endpoint only supports csv")

    odps = build_odps(creds)
    ctx = _open_reader(odps, req)
    filename = export_filename(req.filename or req.table or "export", "csv")

    return StreamingResponse(
        stream_csv(ctx),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/api/export", response_model=ExportTaskResponse)
async def export_task(
    req: ExportRequest,
    creds: OdpsCredentials = Depends(credentials_from_headers),
):
    """xlsx / 大 csv 异步任务入口，返回 task_id 供 SSE 订阅和下载。"""
    odps = build_odps(creds)
    task = task_manager.create(loop=asyncio.get_running_loop())
    filename = export_filename(req.filename or req.table or "export", req.format)
    task.filename = filename
    output_path = make_output_path(task.task_id, filename)

    def job(t):
        try:
            ctx = _open_reader(odps, req)
            task_manager.update(t, total=ctx.total)

            def progress(n: int):
                if t.cancelled.is_set():
                    raise RuntimeError("cancelled")
                task_manager.update(t, processed=n)

            if req.format == "xlsx":
                write_xlsx(ctx, output_path, progress=progress)
            else:
                with open(output_path, "wb") as f:
                    for chunk in stream_csv(ctx, progress=progress):
                        if t.cancelled.is_set():
                            raise RuntimeError("cancelled")
                        f.write(chunk)
            t.file_path = output_path
        except OSError as e:
            # 捕获磁盘空间不足等 IO 错误，给出可操作的提示
            if getattr(e, "errno", None) == 28 or "No space left" in str(e):
                raise RuntimeError(
                    "磁盘空间不足。请设置环境变量 MCBG_EXPORT_DIR 指向大盘（如 D:\\mcbg-export），或清理当前导出目录后重试。"
                ) from e
            raise

    task_manager.run_in_thread(task, job)
    return ExportTaskResponse(task_id=task.task_id)


@router.get("/api/task/{task_id}")
def get_task(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task.snapshot()


@router.get("/api/task/{task_id}/events")
async def task_events(task_id: str, request: Request):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    q = await task_manager.subscribe(task)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snap = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": "update", "data": json.dumps(snap)}
                if snap["status"] in ("done", "failed", "cancelled"):
                    break
        finally:
            task_manager.unsubscribe(task, q)

    return EventSourceResponse(gen())


@router.delete("/api/task/{task_id}")
def cancel_task(task_id: str):
    if not task_manager.cancel(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True}
