from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import schema as schema_route
from .routes import export as export_route
from .routes import download as download_route
from .services.task_manager import cleanup_stale_files, EXPORT_DIR, disk_free_bytes


app = FastAPI(title="MCBG Data Export", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.on_event("startup")
def _startup():
    removed = cleanup_stale_files()
    free_gb = disk_free_bytes(EXPORT_DIR) / (1024 ** 3)
    print(f"[mcbg] export dir: {EXPORT_DIR}")
    print(f"[mcbg] cleaned stale files: {removed}, free space: {free_gb:.1f} GB")
    if free_gb < 5:
        print(f"[mcbg] WARNING: only {free_gb:.1f} GB free. Set MCBG_EXPORT_DIR to a larger disk.")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "mcbg-data-export",
        "export_dir": str(EXPORT_DIR),
        "free_bytes": disk_free_bytes(EXPORT_DIR),
    }


app.include_router(schema_route.router)
app.include_router(export_route.router)
app.include_router(download_route.router)
