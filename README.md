# MCBG Data Export

MaxCompute (ODPS) 数据导出工具：Chrome 扩展 + 本地 Python 服务，支持百万级数据导出为 xlsx / csv。

## 架构

- `extension/` — Chrome 扩展 (Manifest V3)，负责 UI、配置、触发下载
- `server/` — 本地 Python 服务 (FastAPI + pyodps)，走 Tunnel 高速通道流式拉数据

## 快速开始

### 1. 启动后端服务

```bash
cd server
pip install -r requirements.txt
python run.py
```

默认监听 `http://127.0.0.1:19527`。

### 2. 加载扩展

1. 打开 Chrome → 扩展管理 (`chrome://extensions/`)
2. 打开「开发者模式」
3. 点「加载已解压的扩展程序」，选择本项目的 `extension/` 目录
4. 点扩展图标 → 齿轮进入配置页，填入 MaxCompute AccessKey / Endpoint / Project

### 3. 导出数据

点扩展图标 → 输入表名 → 勾选字段 → 选择格式 → 导出。

## 格式说明

- **xlsx**：单 sheet 上限 1,048,575 行数据，超出自动拆分为 Sheet1 / Sheet2 / ...
- **csv**：无行数限制，UTF-8 with BOM（Excel 可直接打开），百万级首选

## 安全说明

AccessKey 保存在浏览器 `chrome.storage.local`，仅做简单混淆，不是真加密。请勿在多人共用的浏览器配置文件中保存生产环境凭据。
