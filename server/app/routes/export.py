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
from ..services.exporter_csv import stream_csv, stream_csv_maybe_gzip
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
    """CSV 专用：零磁盘，直接流式返回给浏览器。
    大数据量自动启用 gzip 压缩（文件名 .csv.gz，Content-Type application/gzip）。
    """
    if req.format != "csv":
        raise HTTPException(status_code=400, detail="streaming endpoint only supports csv")

    odps = build_odps(creds)
    ctx = _open_reader(odps, req)
    base_name = req.filename or req.table or "export"

    stream, is_gzip = stream_csv_maybe_gzip(ctx)
    if is_gzip:
        filename = export_filename(base_name, "csv.gz")
        media_type = "application/gzip"
    else:
        filename = export_filename(base_name, "csv")
        media_type = "text/csv; charset=utf-8"

    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition(filename),
            # 告诉扩展原始格式 + 是否压缩，方便 UI 显示
            "X-MCBG-Compressed": "1" if is_gzip else "0",
            "X-MCBG-Total-Rows": str(ctx.total) if ctx.total is not None else "",
            "X-MCBG-Source-Bytes": str(ctx.size_bytes) if ctx.size_bytes is not None else "",
        },
    )


@router.post("/api/export", response_model=ExportTaskResponse)
async def export_task(
    req: ExportRequest,
    creds: OdpsCredentials = Depends(credentials_from_headers),
):
    """xlsx / 大 csv 异步任务入口，返回 task_id 供 SSE 订阅和下载。

    csv 格式下：行数超阈值时自动 gzip，文件名会是 .csv.gz。
    因为任务创建时还拿不到 total，文件名在 job 里开 reader 后再敲定。
    """
    odps = build_odps(creds)
    task = task_manager.create(loop=asyncio.get_running_loop())
    base_name = req.filename or req.table or "export"

    def job(t):
        try:
            ctx = _open_reader(odps, req)
            task_manager.update(t, total=ctx.total)

            def progress(n: int):
                if t.cancelled.is_set():
                    raise RuntimeError("cancelled")
                task_manager.update(t, processed=n)

            if req.format == "xlsx":
                filename = export_filename(base_name, "xlsx")
                output_path = make_output_path(t.task_id, filename)
                t.filename = filename
                task_manager.update(t, filename=filename)
                write_xlsx(ctx, output_path, progress=progress)
            else:
                # CSV：按行数决定是否压缩，落盘文件名同步变 .csv.gz
                stream, is_gzip = stream_csv_maybe_gzip(ctx, progress=progress)
                ext = "csv.gz" if is_gzip else "csv"
                filename = export_filename(base_name, ext)
                output_path = make_output_path(t.task_id, filename)
                t.filename = filename
                task_manager.update(t, filename=filename)

                with open(output_path, "wb") as f:
                    for chunk in stream:
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
