# CLAUDE.md

本文件给在此仓库工作的 Claude Code 提供上下文。阅读优先级：本文件 > PROJECT_INDEX.md > 其它 README。

## 项目一句话

MaxCompute (ODPS) 数据导出工具：Chrome 扩展 UI + Python 服务端（本地或 ECS），通过 Tunnel 流式导出 xlsx / csv，支持百万级。

## 技术栈速查

- 扩展：Chrome MV3，原生 ES Modules，**无构建系统**，主 UI 走 `sidePanel`，配置走 `options_page`
- 服务端：Python 3.9+，FastAPI + uvicorn，pyodps (`odps`)，pyarrow，xlsxwriter，sse-starlette，pydantic v2
- 部署：systemd + venv（见 `deploy/`）

## 常用命令

```bash
# 服务端（开发）
cd server
pip install -r requirements.txt
python run.py                      # 默认 127.0.0.1:19527

# 服务端（ECS 部署）
sudo bash deploy/ecs_install.sh   # 需先把代码同步到 /root/mcbg-data-export

# 健康检查
curl http://127.0.0.1:19527/api/health
```

扩展没有构建步骤。开发时直接在 `chrome://extensions/` 加载 `extension/` 目录；修改后点「刷新」即可。

## 目录语义

- `extension/sidepanel/` — 主 UI。改字段选择、进度显示、导出路径分叉都在这里
- `extension/options/` — 多 profile 配置页
- `extension/lib/api.js` — 所有后端 HTTP/SSE 调用，改接口时两端都要动
- `extension/lib/storage.js` — `chrome.storage.local` 封装 + AK/SK 的 XOR 混淆（**不是加密**）
- `extension/background/service_worker.js` — 只做两件事：打开 sidePanel、代理 `chrome.downloads.download`
- `server/app/routes/` — FastAPI 路由（薄层），不放业务
- `server/app/services/` — 业务实现。`reader.py` 是关键抽象层
- `server/app/models.py` — pydantic 请求/响应模型
- `deploy/` — systemd unit + 安装脚本
- `mcbg-data-export/` — **历史遗留嵌套子目录，忽略**（只含 `.gitattributes`）

## 关键抽象

### `ReaderContext`（`server/app/services/reader.py`）

统一 Tunnel 下载和 SQL 执行两种数据源。字段：
- `columns: List[ColumnMeta]`
- `total: Optional[int]`
- `iter_records()` — 行级 `List[Any]` 迭代器（xlsx / 回退 CSV 用）
- `iter_arrow_batches()` — pyarrow `RecordBatch` 迭代器，**为 None 表示该源不支持 arrow**，CSV 会回退到 Python 路径

**新增数据源时，实现一个返回 `ReaderContext` 的函数即可**，writer 无需改动。

### 导出路径选择（`server/app/routes/export.py::_open_reader`）

1. `req.sql` → `open_sql_reader`（`execute_sql` + Instance Tunnel）
2. `table` + 无 `where` → `open_table_reader`（直接 TableTunnel）
3. `table` + `where` → 拼 SELECT，走 SQL 路径

### 任务生命周期（`server/app/services/task_manager.py`）

- `create()` → `run_in_thread(task, job)` 起后台 `threading.Thread`
- 线程里 `job(task)` 执行，每次 `update()` 调 `_broadcast`，把 snapshot 通过 `run_coroutine_threadsafe` 塞回事件循环的每个订阅 `asyncio.Queue`
- SSE 在 `/api/task/{id}/events` 从 Queue 取 → yield `{event:"update", data:snapshot}`；`done/failed/cancelled` 后关闭
- 取消：`task.cancelled.Event`，progress 回调里检测到就抛 `RuntimeError("cancelled")` 中断 job

**修改任务状态机时记得：状态必须经 `update()` 设置，否则订阅者收不到。**

## 写代码时的约束

### Python 层

- 不要在请求处理函数里做重 I/O。重逻辑进 `services/`，路由只做参数校验 + 调 service + 组装响应
- **写 CSV/xlsx 的大循环里不要做 Python-level 的 escape**。CSV 已经委托给 pyarrow native，xlsx 绕过 `write()` 直接 `_write_string`，这是热点路径的关键。改写入逻辑要保持这个性质
- xlsx 单 sheet 硬上限 `1,048,575` 数据行，单元格 `32,767` 字符，超限必须拆 sheet / 截断（现有代码已处理）
- prefetch 线程的 `bufsize`：**batch 级用小值 (8~200)**（RecordBatch 对象大），行级可以 4000。`services/reader.py::prefetch`
- 新端点要从 header 读凭据，用 `Depends(credentials_from_headers)`；不要让前端在 body 里传 AK/SK

### 前端层

- 所有后端调用经 `lib/api.js`，**不要在 sidepanel/options 里直接 fetch**
- SSE 订阅用 `subscribeTask`，别自己 new EventSource；它封装了 done/failed/cancelled 后的自动关流
- 下载必须走 `chrome.runtime.sendMessage({type:"download"})` → service worker → `chrome.downloads.download`，因为 sidepanel 没有 `downloads` 权限的调用上下文

### 安全 / 凭据

- 扩展里 AK/SK 仅 XOR 混淆，**不是加密**。Options 页已经有醒目 warning，不要删
- 服务端 `MCBG_CORS_ALLOW_ALL=1` 时接受任意 origin，公网部署必需。本地开发保持默认
- ECS 公网部署是 HTTP 明文，路径上能抓到 AK/SK（见 `deploy/README.md` 安全说明）

## 不要做的事

- 不要给扩展加构建工具（webpack/vite/TS）。保持零依赖 ES Modules
- 不要在 `extension/` 里新增第三方 JS 库（包括 jQuery）
- 不要把 `xlsxwriter` 换成 `openpyxl`（百万级写入会爆内存）
- 不要把 pyarrow 路径删掉退回纯 Python CSV；大 JSON 字段场景差距是数量级的
- 不要给 `/api/export/stream` 加 format=xlsx；xlsx 必须走任务模式，因为需要临时目录 + 预先知道总行数才能合理 constant_memory 写
- `mcbg-data-export/` 子目录是历史遗留，**别往里写东西**

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MCBG_HOST` | `127.0.0.1` | 监听地址（ECS 设 `0.0.0.0`） |
| `MCBG_PORT` | `19527` | 端口（ECS 设 `8080`） |
| `MCBG_EXPORT_DIR` | 系统 TEMP/`mcbg-export` | 导出目录；xlsx 吃盘，必须指向大盘 |
| `MCBG_CORS_ALLOW_ALL` | `0` | 置 `1` 放开所有 origin |

## 测试 / 验证

仓库暂无自动化测试。验收标准：

1. `curl /api/health` 返回 `ok:true`
2. 扩展「测试连接」显示 `✓ 连接正常`
3. 小表导出（xlsx 和 csv 各一次）能成功落盘、文件名带中文不乱码
4. 百万级表导出 CSV：走 `/api/export/stream` 或 `/api/export`，SSE 进度正常递增

UI 改动后本地加载扩展手测 golden path（表模式 + 字段勾选 + 分区选择 + xlsx 下载；SQL 模式 + CSV 直传）。

## 文档

- `README.md` — 用户向快速上手
- `PROJECT_INDEX.md` — 详细项目索引（目录、技术栈、入口、数据流、API）
- `docs/architecture.md` — 架构与关键设计决策说明
- `server/README.md` — 服务端安装 / 磁盘 / 接口
- `deploy/README.md` — ECS 公网部署步骤与安全注意事项
