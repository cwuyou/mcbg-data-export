import os
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("MCBG_HOST", "127.0.0.1")
    port = int(os.environ.get("MCBG_PORT", "19527"))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
