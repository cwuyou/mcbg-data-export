from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services.task_manager import task_manager


router = APIRouter()


@router.get("/api/download/{task_id}")
def download(task_id: str):
    task = task_manager.get(task_id)
    if not task or not task.file_path:
        raise HTTPException(status_code=404, detail="file not ready")
    filename = task.filename or "export"
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
    )
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if filename.endswith(".xlsx")
        else "application/gzip"
        if filename.endswith(".csv.gz")
        else "text/csv; charset=utf-8"
    )
    return FileResponse(
        task.file_path,
        media_type=media,
        headers={"Content-Disposition": disposition},
    )
