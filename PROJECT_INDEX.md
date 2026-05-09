# PROJECT_INDEX

MaxCompute (ODPS) 数据导出工具：Chrome 扩展 (UI) + 本地 / ECS Python 服务 (拉数写盘)，通过 Tunnel 流式导出 xlsx / csv，支持百万级。

## 目录结构

```
mcbg-data-export/
├── extension/            Chrome 扩展 (Manifest V3, 侧边栏形态)
│   ├── manifest.json
│   ├── background/service_worker.js      打开 sidePanel + 代理 downloads.download
│   ├── sidepanel/                        主 UI：表/SQL 两种模式、进度条、SSE 订阅
│   ├── options/                          多 profile 配置页 (服务地址 + AK/SK/Endpoint/Project)
│   ├── lib/
│   │   ├── api.js                        服务端 HTTP/SSE 客户端
│   │   └── storage.js                    chrome.storage.local + XOR 混淆 (非加密)
│   └── icons/
├── server/               FastAPI + pyodps 服务端
│   ├── run.py            uvicorn 启动入口；MCBG_HOST / MCBG_PORT
│   ├── requirements.txt  fastapi / uvicorn / pyodps / xlsxwriter / sse-starlette / pyarrow
│   └── app/
│       ├── main.py       app 装配：CORS + 启动清理 + /api/health + 挂 router
│       ├── models.py     pydantic：SchemaRequest/Response、ExportRequest、TaskStatus 等
│       ├── routes/
│       │   ├── schema.py   /api/schema、/api/partitions
│       │   ├── export.py   /api/export、/api/export/stream、/api/task/*、SSE 进度
│       │   └── download.py /api/download/{id}
│       └── services/
│           ├── odps_client.py   从 HTTP header 读凭据 → 构建 ODPS 实例
│           ├── reader.py        Tunnel / SQL Instance 两种数据源统一成 ReaderContext
│           ├── exporter_csv.py  pyarrow.csv native 快速路径 + Python csv 回退
│           ├── exporter_xlsx.py xlsxwriter constant_memory，超 1,048,575 行自动拆 sheet
│           └── task_manager.py  线程池执行 + asyncio.Queue 广播 + 磁盘清理
├── deploy/               公网 ECS 部署
│   ├── ecs_install.sh            装 venv / 建用户 / 注册 systemd
│   ├── mcbg-data-export.service  systemd unit (端口 8080，MCBG_CORS_ALLOW_ALL=1)
│   └── README.md                 部署步骤 + 安全注意
├── README.md
└── PROJECT_INDEX.md      (本文件)
```

> `mcbg-data-export/` 子目录内只有 `.gitattributes`，是历史遗留的嵌套工作区，非代码。

## 技术栈

**扩展端 (Chrome MV3)**
- 原生 JS (ES modules)，无构建、无框架
- `sidePanel` API 作为主 UI、`options_page` 作配置、`service_worker` 仅代理 `chrome.downloads`
- 凭据存于 `chrome.storage.local`，AK/SK 做 XOR + base64 混淆（非加密）
- 进度用 `EventSource` 订阅 SSE

**服务端 (Python 3.9+)**
- FastAPI + uvicorn，`sse-starlette` 推进度，pydantic v2 做 schema
- pyodps (`odps` 包) 走 **TableTunnel / Instance Tunnel**；优先 `open_arrow_reader` 拿到 pyarrow RecordBatch
- CSV：pyarrow `CSVWriter` native (C 扩展，释放 GIL) → 失败回退 Python `csv`
- xlsx：`xlsxwriter` `constant_memory=True`，热点路径绕过 `write()` 直接 `_write_string`
- 读写并行：后台线程 prefetch iterator，主线程写盘 (`services/reader.py::prefetch`)
- 任务模型：后台 `threading.Thread` 执行导出，通过 `asyncio.run_coroutine_threadsafe` 把进度塞回事件循环的 Queue

**部署**
- systemd + venv (`deploy/mcbg-data-export.service`)
- ECS 杭州 region，走 MaxCompute 内网 endpoint 免流量费
- 导出目录默认 `/var/lib/mcbg-export`，xlsx 临时 XML 占盘量大，单进程 `MemoryMax=6G`

## 关键入口

### 扩展
- `extension/manifest.json` — 权限：`storage` / `downloads` / `sidePanel`；`host_permissions: http(s)://*/*`
- `extension/background/service_worker.js` — 点图标开侧边栏、接收 `{type:"download"}` 走 `chrome.downloads.download`
- `extension/sidepanel/sidepanel.js:207` `startExport()` — 决定走直传流 / 异步任务
- `extension/sidepanel/sidepanel.js:242` `directDownloadCsv()` — SQL + CSV 或无筛选表 + CSV 直接 blob 下载
- `extension/lib/api.js` — 所有后端调用；`authHeaders()` 注入 `X-ODPS-*`
- `extension/lib/storage.js:33` `loadSettings()` / `:44` `saveSettings()` — profile CRUD + 混淆

### 服务端
- `server/run.py` — `uvicorn.run("app.main:app", host, port)`
- `server/app/main.py:37` `_startup()` — 启动时清 24h 过期文件 + 打印空闲盘
- `server/app/routes/export.py:73` `POST /api/export` — 任务入口
- `server/app/routes/export.py:53` `POST /api/export/stream` — CSV 零落盘流式
- `server/app/routes/export.py:124` `GET /api/task/{id}/events` — SSE 进度推送
- `server/app/services/reader.py:80` `open_table_reader()` — Tunnel 下载 session
- `server/app/services/reader.py:133` `open_sql_reader()` — `execute_sql` + Instance Tunnel
- `server/app/services/task_manager.py:105` `run_in_thread()` — 在后台线程跑 job，生命周期自动广播
- `server/app/services/odps_client.py:16` `credentials_from_headers()` — FastAPI 依赖，从 header 取凭据

## 数据流

```
用户
  │
  ▼
[Chrome 侧边栏]  sidepanel.js
  │  ① 加载 schema / partitions         → POST /api/schema、/api/partitions
  │  ② 点「开始导出」，构造 payload
  │
  ├── CSV 直传 (SQL 模式，或表模式无 where / 未加载 schema)
  │     └── POST /api/export/stream  ──→  fetch().blob() → URL.createObjectURL
  │                                       → sendMessage({type:"download"}) → service_worker
  │                                       → chrome.downloads.download 落盘
  │
  └── 异步任务 (xlsx 或带 schema/where 的 csv)
        ① POST /api/export           ──→ 返回 task_id
        ② EventSource /api/task/{id}/events
        │       server 端 TaskManager 每次 update() 把 snapshot 塞进 asyncio.Queue
        │       sse-starlette yield {"event":"update", data: JSON}
        │    扩展收到 status="done" → chrome.runtime.sendMessage({type:"download",
        │                               url: serverUrl + /api/download/{id}})
        └── service_worker → chrome.downloads.download → 文件落盘

[服务端 导出线程 job(task)]  routes/export.py::job
  ctx = open_sql_reader / open_table_reader
         │
         │ pyodps
         ▼
  ┌───────────────────────────────────────────────┐
  │ ReaderContext                                 │
  │   - iter_arrow_batches (pyarrow RecordBatch)  │  ← 首选
  │   - iter_records        (List[Any])           │  ← 回退 / xlsx
  └───────────────────────────────────────────────┘
         │
         ├── format=csv  → exporter_csv.stream_csv
         │       arrow 可用：pyarrow.csv.CSVWriter → bytes (UTF-8 BOM)
         │       回退       ：Python csv.writer + StringIO buffer
         │       → /api/export/stream：yield 给 StreamingResponse
         │       → /api/export      ：写入 output_path (EXPORT_DIR/{task_id}__{name})
         │
         └── format=xlsx → exporter_xlsx.write_xlsx
                 xlsxwriter Workbook(constant_memory=True, tmpdir=同级目录)
                 row_in_sheet > 1,048,575 → 自动 new_worksheet("Sheet2"...)
                 _write_string 绕过类型判断，每 N 行回调 progress(n)
         每写入若干行 → task_manager.update(processed=n) → _broadcast → SSE
```

### 线程 / 事件循环
- FastAPI 事件循环：接收请求、推 SSE、创建任务
- 任务线程 (`threading.Thread`)：执行 pyodps I/O + 写盘
- prefetch 线程 (`services/reader.py:36`)：下载 RecordBatch / record 放入 `queue.Queue`，写线程消费
- 线程 → 事件循环：`asyncio.run_coroutine_threadsafe(q.put(snap), task.loop)`
- 取消：`task.cancelled.Event` → progress 回调检测 → 抛 RuntimeError 结束 job

## API 结构

所有接口走同源 HTTP，凭据通过 header 传：
- `X-ODPS-Access-Id`
- `X-ODPS-Access-Key`
- `X-ODPS-Endpoint`
- `X-ODPS-Project`

| Method | Path | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/api/health` | — | `{ok, service, export_dir, free_bytes}` | 健康检查 + 盘空间 |
| POST | `/api/schema` | `{table, partition?}` | `SchemaResponse` (columns / partition_columns / row_count / size_bytes) | 表结构 + 容量估算 |
| POST | `/api/partitions` | `{table}` | `{partitions: string[]}` | 分区列表，按字符串倒序 |
| POST | `/api/export` | `ExportRequest` | `{task_id}` | 创建异步任务 (xlsx / csv 落盘) |
| POST | `/api/export/stream` | `ExportRequest` (format=csv) | `text/csv; charset=utf-8` (chunked) | CSV 零落盘直传，不建任务 |
| GET | `/api/task/{id}` | — | `TaskStatus` 快照 | 轮询任务状态 |
| GET | `/api/task/{id}/events` | — | `text/event-stream` | SSE：`update` 事件推 snapshot，`ping` 15s 心跳；`done/failed/cancelled` 后关闭 |
| GET | `/api/download/{id}` | — | `FileResponse` (xlsx / csv) | Content-Disposition 带 RFC 5987 UTF-8 文件名 |
| DELETE | `/api/task/{id}` | — | `{ok: true}` | 置 `cancelled` Event，下次 progress 检测时中断 |

### ExportRequest 形态
```
{
  table?: string,              // 表模式必填（或走 sql）
  partition?: string,          // 形如 "dt=20260501" / "dt=20260501,region=cn"
  columns?: string[],          // 不传 = 全字段
  where?: string,              // 附加 WHERE 片段（触发走 SQL 路径）
  sql?: string,                // 自定义 SQL（走 Instance Tunnel）
  format: "xlsx" | "csv",
  filename?: string
}
```

路径选择 (`routes/export.py:38 _open_reader`)：
1. `req.sql` 存在 → `open_sql_reader` (execute_sql + Instance Tunnel)
2. 只给 `table` 无 `where` → `open_table_reader` (TableTunnel，直接拉分区/全表)
3. `table` + `where` → 拼 SELECT SQL 走 Instance Tunnel

### 前端导出路径决策（v0.2.1）

`sidepanel.js::startExport` 只看格式：

| 用户场景 | 端点 | 文件名 | 进度 |
|---|---|---|---|
| CSV（任意模式，小） | `/api/export/stream` | `xxx.csv` | 字节计数 |
| CSV（行数≥50万 或 存储≥500MB） | `/api/export/stream` | `xxx.csv.gz` | 字节计数 + "已压缩" |
| xlsx | `/api/export` + SSE | `xxx.xlsx` | 百分比 |

**核心原则**：CSV 全部零落盘 stream，xlsx 全部走任务模式落盘。详见 `docs/architecture.md §4.7`。

### TaskStatus 快照
```
{
  task_id: string,
  status: "pending" | "running" | "done" | "failed" | "cancelled",
  processed: int,
  total: int | null,       // 来自 session.count / reader.count
  message: string | null,  // 失败原因
  filename: string | null,
  download_url: "/api/download/{id}" | null
}
```

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `MCBG_HOST` | `127.0.0.1` | 监听地址，ECS 部署设 `0.0.0.0` |
| `MCBG_PORT` | `19527` | 监听端口，ECS 部署设 `8080` |
| `MCBG_EXPORT_DIR` | 系统 TEMP + `mcbg-export` | 导出落盘目录；xlsx 临时文件吃盘，务必指向大盘 |
| `MCBG_CORS_ALLOW_ALL` | `0` | 置 `1` 放开全部 origin（公网部署必需），否则仅允许 `chrome-extension://*` / localhost |

## 已知边界 / 注意点

- **xlsx 硬上限**：单 sheet `1,048,575` 数据行，超出自动拆 `Sheet1/Sheet2/...`；单元格 `32767` 字符，超长截断加 `...[TRUNCATED]`
- **大 JSON / 长文本字段**：强烈推荐 CSV，走 pyarrow native 比 xlsx 快一个数量级
- **凭据安全**：扩展端仅 XOR 混淆；服务端 header 传递，`MCBG_CORS_ALLOW_ALL=1` 后任意 IP 可调（依赖 AK/SK 自身校验）
- **磁盘**：任务文件 24h 后下次启动清理 (`cleanup_stale_files`)；空闲 < 5 GB 时启动日志 WARNING
- **取消**：仅通过 progress 回调检测 `cancelled` Event，如果任务卡在 pyodps 下载 I/O 里不会立即响应
