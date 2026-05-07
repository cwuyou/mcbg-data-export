# MCBG Data Export — 本地服务

## 安装

```bash
cd server
pip install -r requirements.txt
```

## 启动

- Windows: 双击 `run.bat`，或在命令行 `python run.py`
- Linux/macOS: `bash run.sh`，或 `python run.py`

默认监听 `http://127.0.0.1:19527`。保持窗口打开即可。

## 导出目录 / 磁盘空间

xlsx 导出走 `constant_memory` 模式，会在临时目录写大量中间文件（单 sheet 一个 XML + shared string 缓存），**百万行 + 大字段可能临时占用十几 GB**。默认目录是系统 TEMP（Windows 上通常是 C 盘），容易撑爆。

**推荐设置环境变量指向大盘**：

Windows PowerShell：
```powershell
$env:MCBG_EXPORT_DIR = "D:\mcbg-export"
python run.py
```

Windows cmd：
```cmd
set MCBG_EXPORT_DIR=D:\mcbg-export
python run.py
```

Linux / macOS：
```bash
export MCBG_EXPORT_DIR=/data/mcbg-export
python run.py
```

启动日志会打印导出目录和剩余空间，空间 < 5GB 时警告。
生成的文件在 24 小时后下次启动时会被清理。

## 性能提示

- **大 JSON / 长文本字段强烈推荐选 CSV 格式**。CSV 走 pyarrow 原生 C 实现，释放 GIL，比 xlsx 快一个数量级。xlsx 必须按 Excel 规范做 XML 转义，纯 Python 层逐字符处理，对大字段是硬伤
- xlsx 单 sheet 硬上限 1,048,576 行，超过会自动拆 Sheet1 / Sheet2 / ...

## 接口

| Method | Path | 说明 |
|---|---|---|
| GET | /api/health | 健康检查 |
| POST | /api/schema | 获取表结构，入参 `{table, partition?}` |
| POST | /api/partitions | 获取分区列表，入参 `{table}` |
| POST | /api/export | 创建异步导出任务（xlsx/csv），返回 `task_id` |
| POST | /api/export/stream | CSV 直接流式响应（无任务） |
| GET | /api/task/{id} | 查询任务快照 |
| GET | /api/task/{id}/events | SSE 进度推送 |
| GET | /api/download/{id} | 下载生成的文件 |
| DELETE | /api/task/{id} | 取消任务 |

所有接口需通过 HTTP header 传递凭据：
- `X-ODPS-Access-Id`
- `X-ODPS-Access-Key`
- `X-ODPS-Endpoint`
- `X-ODPS-Project`

## 手动测试

```bash
# 健康检查
curl http://127.0.0.1:19527/api/health

# 导出 CSV (直接落盘)
curl -X POST http://127.0.0.1:19527/api/export/stream \
  -H 'Content-Type: application/json' \
  -H 'X-ODPS-Access-Id: YOUR_ID' \
  -H 'X-ODPS-Access-Key: YOUR_KEY' \
  -H 'X-ODPS-Endpoint: http://service.cn-hangzhou.maxcompute.aliyun.com/api' \
  -H 'X-ODPS-Project: your_project' \
  -d '{"table":"my_table","format":"csv"}' \
  -o test.csv
```

## 打包（后续）

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --name mcbg-export-server run.py
```

生成的单文件 exe 位于 `dist/`。
