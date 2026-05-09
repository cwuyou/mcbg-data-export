# 架构设计

MCBG Data Export 的架构说明、关键设计决策与权衡。本文档聚焦"为什么这么设计"，目录/入口/API 清单见 [PROJECT_INDEX.md](../PROJECT_INDEX.md)。

## 1. 整体架构

```
┌─────────────────────────┐        HTTP + SSE         ┌──────────────────────────┐
│  Chrome 扩展 (MV3)      │ ─────────────────────────> │   Python 服务端          │
│                         │  X-ODPS-* header 携带凭据 │   FastAPI + uvicorn      │
│  ┌───────────────────┐  │                            │   本地 127.0.0.1:19527   │
│  │ sidepanel (主UI)  │  │                            │   或 ECS :8080           │
│  │ options (配置)    │  │                            │                          │
│  │ service_worker    │  │                            │        │                 │
│  │  (下载代理)       │  │                            │        ▼                 │
│  └───────────────────┘  │                            │   pyodps Tunnel          │
│  chrome.storage.local   │                            │        │                 │
│  保存 AK/SK (XOR 混淆)  │                            │        ▼                 │
└─────────────────────────┘                            │   MaxCompute (阿里云)    │
                                                       └──────────────────────────┘
                                                             (同 region 走内网)
```

**核心分工**
- 扩展：UI、凭据管理、触发下载。**不直接访问 MaxCompute**
- 服务端：用扩展传来的 AK/SK 调 pyodps，拉数据、做格式转换、落盘或流式响应
- 浏览器下载：Chrome 原生 `chrome.downloads` 负责，避免 sidepanel 里大 blob 占内存

## 2. 为什么是这种形态

### 2.1 为什么要本地 / ECS 服务端，而不是扩展直连 MaxCompute？

- **Tunnel 协议复杂**：pyodps 的 TableTunnel 是阿里云内部协议，纯 JS 没有成熟实现
- **CORS 限制**：即便 HTTP 层能实现，MaxCompute Endpoint 不会给浏览器放 CORS
- **百万级数据处理**：浏览器 Blob 有内存限制，一次性 100W 行 xlsx 会爆；必须服务端流式
- **xlsx 生成**：xlsxwriter 是 Python 侧最稳的流式 xlsx 库，JS 侧找不到同量级的

### 2.2 为什么是 Chrome 扩展而不是 Web 页面？

- **用户侧零部署**：装扩展即用，不用自己起前端
- **侧边栏常驻**：`sidePanel` 切标签不中断，方便对着目标页面导数
- **`chrome.downloads` 原生下载**：文件名中文、大文件分片都不用操心

## 3. 服务端分层

```
routes/     ← 薄层：参数校验 + 依赖注入 + 组装响应
services/   ← 业务：读取抽象、writer、任务管理
models.py   ← pydantic 请求/响应 schema
main.py     ← app 装配（CORS、启动钩子、/api/health、挂 router）
```

**设计原则**：路由薄、service 厚。路由里不写 `for row in reader` 这种循环。

## 4. 关键设计决策

### 4.1 `ReaderContext`：统一抽象两种数据源

`services/reader.py` 把 TableTunnel 和 SQL Instance Tunnel 包装成同一个 `ReaderContext`，对 writer 透明。

```
┌─────────────┐                ┌─────────────────┐
│ Table 模式  │──open_table_…─>│                 │
└─────────────┘                │ ReaderContext   │──> csv writer
                               │  iter_records   │──> xlsx writer
┌─────────────┐                │  iter_arrow_…   │
│  SQL 模式   │──open_sql_…───>│                 │
└─────────────┘                └─────────────────┘
```

**为什么这么设计**：
- Writer 只关心"给我行 / 给我 arrow batch"，不关心数据从哪来
- 新增数据源（例如未来要支持 ODPS volume、外部表）只需要返回 `ReaderContext`
- arrow batches 是"可选特性"：SQL 模式在老版本 pyodps 可能拿不到 arrow reader，此时 `iter_arrow_batches=None`，CSV writer 会自动回退

**代价**：`iter_records` 的每行要做一次 `List[Any]` 构造，CPU 开销比直接消费 Record 略高。但相对于 pyodps 网络下载时间可忽略。

### 4.2 CSV 两条路径：pyarrow native + Python 回退

```
ctx.iter_arrow_batches != None ?
  ├── yes → pyarrow.csv.CSVWriter   (C 扩展，释放 GIL，UTF-8 编码/escape 在 C 层)
  │         失败 → 回退
  └── no  → Python csv.writer + StringIO
            (逐字段 _format_cell, 每 5000 行 flush)
```

**为什么双路径**：
- 业务场景有大量 JSON / 长文本字段。Python `csv` 模块逐字符处理 UTF-8 + quote，大字段吞吐极差（实测差 3~10×）
- pyarrow CSV writer 是 C 实现，把 escape + 编码搬出 GIL
- 但 pyarrow 路径依赖 session 能开 arrow reader；老 pyodps / 某些 SQL 结果拿不到，必须回退

**实现坑**：pyarrow `BufferOutputStream` 不支持 truncate。流式输出每写一个 batch 就要重建 writer + buffer（`exporter_csv.py:56~72`），并且第二次起的 writer 要关 header。

### 4.3 xlsx：constant_memory + 绕过 write()

```python
xlsxwriter.Workbook(path, {"constant_memory": True, "tmpdir": ...})
# 热点：绕过 write() 的类型判断，直接 _write_string
worksheet._write_string(row_in_sheet, col, _format_cell(row[col]), None)
```

- `constant_memory=True`：每 flush 一行就从内存卸掉，否则百万行会 OOM
- `tmpdir` 指向 `output_path` 所在盘，避免临时文件和最终文件跨盘 move（Windows 跨盘 rename 会变成 copy）
- `_write_string` 直调：`write()` 每行做类型判断（数字？公式？URL？字符串？），百万级下累计显著。上游已经 `_format_cell` 保证都是 str，可以跳过

**限制与处理**：
- 单 sheet 数据行上限 `1,048,575` → 超限自动 `Sheet2`、`Sheet3`…
- 单元格上限 `32,767` 字符 → 超长截断加 `...[TRUNCATED]` 标记

**为什么不用 openpyxl**：write-only 模式也比 xlsxwriter constant_memory 慢且吃内存。xlsxwriter 对大文件是社区共识。

### 4.4 读写并行：prefetch 队列

```
[pyodps 下载线程] ── queue.Queue ──> [主线程: writer]
       producer                         consumer
```

`services/reader.py::prefetch` 把任意迭代器套一层后台线程。下载和写盘重叠执行，而不是"下 → 写 → 下 → 写"串行。

**bufsize 策略**：
- arrow batch 级（CSV 快路径）：`bufsize=8`。batch 本身大，缓太多吃内存
- 行级（xlsx / CSV 回退）：`bufsize=4000`。行小，多缓一点让下载充分领先

### 4.5 异步任务 + SSE 进度

```
[HTTP POST /api/export]
   task = TaskManager.create()
   threading.Thread(target=job, task).start()     ← 后台线程跑导出
   return {task_id}

[HTTP GET /api/task/{id}/events]                  ← SSE
   q = await task.subscribe()
   while not disconnected:
       snap = await q.get()                        ← 事件循环 Queue
       yield {event:"update", data: snap}
       if terminal: break
```

**跨线程传递状态**：

```python
# services/task_manager.py::_broadcast
asyncio.run_coroutine_threadsafe(q.put(snapshot), task.loop)
```

导出线程 → 事件循环：用 `run_coroutine_threadsafe` 把 `Queue.put` 调度回主事件循环。这是 asyncio 跨线程通信的标准方式，线程安全。

**为什么不用 WebSocket**：
- SSE 单向够用（服务端 → 浏览器）
- EventSource 是浏览器原生 API，扩展端不需要库
- SSE 天然带自动重连（虽然我们的任务不支持续传，断了就完了）

**为什么还要 `GET /api/task/{id}`**：SSE 断了或扩展重启后，快照查询是兜底。前端当前没用上，但接口保留。

### 4.6 取消语义

`task.cancelled` 是 `threading.Event`。DELETE `/api/task/{id}` 只设置 Event，不强杀线程。

```python
def progress(n):
    if t.cancelled.is_set():
        raise RuntimeError("cancelled")
    task_manager.update(t, processed=n)
```

writer 每 N 行回调 progress，取消生效时机 = 下一次 progress 调用。

**权衡**：
- ✅ 简单、无需 thread kill 的黑魔法（Python 不支持）
- ✅ 线程资源自然回收（抛出 → finally 关 workbook / close reader）
- ❌ 如果线程卡在 pyodps 下载 I/O 里（网络慢、服务端 hang），取消要等 I/O 返回才感知

### 4.7 两种导出路径：`/api/export` vs `/api/export/stream`

| | `/api/export` | `/api/export/stream` |
|---|---|---|
| 格式 | xlsx 或 csv | 仅 csv |
| 响应 | `{task_id}`，后续 SSE | `text/csv` 或 `application/gzip` chunked，直接下载 |
| 落盘 | 是（EXPORT_DIR/{id}__{name}）| 否，纯流式 |
| 进度 | SSE 实时推送（百分比） | 仅字节计数（没有总数） |
| 取消 | DELETE 任务接口 | 浏览器侧 `AbortController` |
| 适用 | 需要落盘的 xlsx | 所有 CSV |

#### 前端路径选择（`sidepanel.js::startExport`，v0.2.1）

经过简化后只剩两条规则：

```
format === 'csv'   → /api/export/stream  （零落盘）
format === 'xlsx'  → /api/export         （任务模式）
```

**行为矩阵**：

| 用户场景 | 路径 | 文件名 | 进度形式 |
|---|---|---|---|
| 表 + CSV（小） | stream | `xxx.csv` | 字节计数 + 条纹动画 |
| 表 + CSV（大，行数≥50万 或 存储≥500MB） | stream | `xxx.csv.gz` | 同上，UI 标注"已压缩" |
| 表 + xlsx | 任务 | `xxx.xlsx` | SSE 百分比 |
| SQL + CSV | stream | `xxx.csv` 或 `xxx.csv.gz` | 字节计数 |
| SQL + xlsx | 任务 | `xxx.xlsx` | SSE 百分比 |

#### 为什么 CSV 一律走 stream

- **不占 ECS 磁盘**：40GB 系统盘扛不住单次 30 GB 的 CSV（一张 IM 大表就够撑爆），落盘方案太脆弱
- **少一个状态机**：CSV 任务不需要 task_id / SSE 订阅 / 文件 GC，链路更短，故障面更小
- **流式不影响压缩**：gzip 在 stream 路径上也生效，体积小、3 Mbps 公网下载更快
- **取消语义对等**：浏览器 `AbortController` 一断流，FastAPI 也会停止生成器
- **代价**：失去百分比进度条。但 CSV 总量在写入前未必能拿到（SQL 模式更明显），百分比本就不准；能看到字节在涨即可

#### 为什么 xlsx 必须走任务

- **临时文件吃盘**：xlsxwriter `constant_memory` 模式每个 sheet 都需要本地临时 XML
- **必须先写完才能 zip 成 .xlsx**：流式 xlsx 不可行（zip central directory 在文件末尾）
- **总行数提前已知**（来自 `session.count`），SSE 进度条做得出百分比，体验明显优于字节计数

### 4.8 凭据传递：HTTP Header

所有需要凭据的端点用 FastAPI `Depends(credentials_from_headers)` 从 4 个 header 取：

```
X-ODPS-Access-Id
X-ODPS-Access-Key
X-ODPS-Endpoint
X-ODPS-Project
```

**为什么不是 body / query string**：
- Query string 会进入 access log，AK/SK 必然泄漏
- Body 放凭据会和业务 schema 耦合，每个端点的 pydantic model 都要带这 4 个字段
- Header 与 body 职责分离，body 只描述业务请求

**HTTPS 缺失的后果**：ECS 公网部署走 HTTP 明文，路径上能抓到 header。文档已经醒目警告（`deploy/README.md §安全说明`）。下一步优化按优先级是：加 API Key 鉴权 → 加 HTTPS → 前置 Nginx 限流。

### 4.9 扩展端：无构建 + MV3 sidePanel

- **无构建系统**：原生 ES Modules，改完即生效。代价是不能用 TS / 新语法（现代 Chrome 够用）
- **sidePanel 而非 popup**：popup 点外面就关，用户切到目标标签查对照时 popup 会消失；sidePanel 常驻当前窗口，更契合导数场景
- **service worker 只做下载代理**：sidepanel 用 `fetch().blob()` + `URL.createObjectURL` 拿到 URL，通过 `runtime.sendMessage` 让 worker 调 `chrome.downloads.download`。因为 sidepanel 上下文直接调 downloads 在某些版本行为不稳，worker 更可靠

### 4.10 凭据存储：XOR 混淆（非加密）

`extension/lib/storage.js` 对 AK/SK 做 XOR + base64，存 `chrome.storage.local`。

**这不是安全机制**。目的只是让随手打开 storage 的人看不到明文，防止凭据被路人截图看到。真正的安全边界在：
- 用户自己的操作系统账号隔离
- MaxCompute 的 AK/SK 权限模型（可以给只读子账号）

options 页面有醒目 warning 告诉用户不要在公共机器存生产凭据。

## 5. 部署拓扑

### 5.1 本地开发

```
[Chrome 扩展] ──> 127.0.0.1:19527 [本地 Python]  ──> MaxCompute
```

- 用户自己装 Python + 起服务
- 只有本机能访问服务
- CORS 默认仅放 `chrome-extension://*` 和 localhost

### 5.2 ECS 公网共享

```
[多人 Chrome 扩展] ──公网 HTTP──> ECS:8080 ──> MaxCompute 内网
                                    │
                                    └── systemd + venv + mcbg 系统用户
```

- 一台 ECS 服务多人，各自在扩展里填自己的 AK/SK
- ECS 与 MaxCompute 同 region (杭州) → endpoint 用 `aliyun-inc.com` 免流量费
- `MCBG_CORS_ALLOW_ALL=1` 放所有 origin
- 已知风险：HTTP 明文、无限流、无服务端鉴权（见 `deploy/README.md`）

## 6. 未来扩展点

按优先级列出来，方便下次 pick up：

1. **服务端 API Key 鉴权** — 前置一层简单 token，防陌生 IP 调用
2. **HTTPS（域名 + Let's Encrypt）** — 堵住 AK/SK 明文传输
3. **Nginx 前置限流 + 磁盘水位告警**
4. **分区扫描预估行数** — 现在 `/api/schema` 对分区表全表 `record_num` 不准，改成可选扫多个分区聚合
5. **断点续导 / 恢复** — SSE 断了就断了，长任务用户要刷新重来；可以让前端本地持久化 `task_id` 支持重连
6. **xlsx 列宽自适应** — 现在固定，长字段看起来挤
7. **导出时的过滤表达式校验** — 现在 `where` 是拼接 SQL 直接丢给 MaxCompute，语法错误要跑起来才报
