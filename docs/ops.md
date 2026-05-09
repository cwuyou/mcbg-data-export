# 运维速查手册

日常维护 MCBG Data Export 用得上的命令。分 Windows（开发机 / 扩展打包）和 Linux（ECS 服务端）两组。

## 一、ECS 服务端（Linux）

### 1.1 服务状态

```bash
# 服务当前状态（active/inactive/failed + 最近 10 行日志）
systemctl status mcbg-data-export

# 简洁判断：只输出 active / inactive
systemctl is-active mcbg-data-export

# 是否开机自启
systemctl is-enabled mcbg-data-export

# 查看进程
ps -ef | grep '[m]cbg'

# 监听端口
ss -tlnp | grep 8080
```

### 1.2 启动 / 停止 / 重启

```bash
# 启动
systemctl start mcbg-data-export

# 停止
systemctl stop mcbg-data-export

# 重启（改配置后用）
systemctl restart mcbg-data-export

# 开机自启
systemctl enable mcbg-data-export

# 取消开机自启
systemctl disable mcbg-data-export

# 改 service 文件后必须 reload
systemctl daemon-reload
systemctl restart mcbg-data-export
```

### 1.3 日志

```bash
# 实时看日志（类似 tail -f）
journalctl -u mcbg-data-export -f

# 最近 100 行
journalctl -u mcbg-data-export -n 100 --no-pager

# 今天的日志
journalctl -u mcbg-data-export --since today

# 某个时间段
journalctl -u mcbg-data-export --since "2026-05-08 14:00" --until "2026-05-08 16:00"

# 只看错误级别
journalctl -u mcbg-data-export -p err

# 清理旧日志（默认 systemd 自动清，手动强制）
journalctl --vacuum-time=7d    # 只保留 7 天
journalctl --vacuum-size=500M  # 总量上限 500M
```

### 1.4 健康检查

```bash
# 本机自测
curl http://127.0.0.1:8080/api/health

# 带详细头
curl -v http://127.0.0.1:8080/api/health

# 从外部测（另一台机器）
curl http://<ECS公网IP>:8080/api/health
```

### 1.5 磁盘 / 导出目录

```bash
# 导出目录占用
du -sh /var/lib/mcbg-export/
du -sh /var/lib/mcbg-export/* 2>/dev/null | sort -h | tail

# 根分区空间
df -h /

# 手动清理导出文件（全删）
rm -f /var/lib/mcbg-export/*

# 清理 3 小时前的（hourly cron 已在做，手动触发）
find /var/lib/mcbg-export -type f -mmin +180 -delete

# 查看自动清理 cron
cat /etc/cron.hourly/mcbg-cleanup

# swap 使用情况
swapon --show
free -h
```

### 1.6 更新代码

```bash
# 场景 A：本地 scp 过来
# 前置（仅首次）：在 ECS 上建好中间目录
# mkdir -p /root/mcbg-data-export/server

# ⚠ 重要：推到 /root 中转时必须同步完整 server 目录，而不是只推 app/
# 因为下面的 rsync --delete 会按源目录内容裁剪目标目录，
# 源缺 run.py / requirements.txt 会把 /opt 下的一起删掉
# （Windows PowerShell 执行）
# scp -r D:\ai_code\mcbg-data-export\server root@<ECS>:/root/mcbg-data-export/

# 然后 ECS 上：
cd /root/mcbg-data-export
sudo rsync -a --delete --exclude '__pycache__' --exclude '.venv' \
    server/ /opt/mcbg-data-export/server/
sudo chown -R mcbg:mcbg /opt/mcbg-data-export/server
sudo systemctl restart mcbg-data-export
sudo journalctl -u mcbg-data-export -f   # 确认起来了 Ctrl+C 退出

# 场景 B：只改了依赖 requirements.txt
cd /opt/mcbg-data-export/server
sudo -u mcbg .venv/bin/pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
sudo systemctl restart mcbg-data-export
```

### 1.7 Python 环境（服务端 venv）

```bash
VENV=/opt/mcbg-data-export/server/.venv

# 看 venv 的 Python 版本
$VENV/bin/python --version

# 看装了哪些包
$VENV/bin/pip list

# 检查关键依赖装了没
$VENV/bin/python -c 'import uvicorn, fastapi, odps, xlsxwriter, pyarrow; print("OK")'

# 单个包的版本
$VENV/bin/pip show fastapi

# 升级某个包
sudo -u mcbg $VENV/bin/pip install -U fastapi -i https://mirrors.aliyun.com/pypi/simple/

# venv 坏了要重建
systemctl stop mcbg-data-export
sudo rm -rf /opt/mcbg-data-export/server/.venv
sudo -u mcbg python3.11 -m venv /opt/mcbg-data-export/server/.venv
sudo -u mcbg /opt/mcbg-data-export/server/.venv/bin/pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
sudo -u mcbg /opt/mcbg-data-export/server/.venv/bin/pip install -r /opt/mcbg-data-export/server/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
systemctl start mcbg-data-export
```

### 1.8 调试（服务挂了排障）

```bash
# 前台手动跑，真实报错直接打在终端
cd /opt/mcbg-data-export/server
sudo -u mcbg -E env MCBG_HOST=0.0.0.0 MCBG_PORT=8080 \
    .venv/bin/python run.py

# 查看进程打开的文件（比如确认日志在写哪里）
pid=$(systemctl show -p MainPID --value mcbg-data-export)
ls -l /proc/$pid/fd/ | head

# 看进程用的 Python 可执行文件
readlink -f /proc/$pid/exe

# 内存占用
ps -o pid,rss,cmd -p $pid

# 看文件句柄数
ls /proc/$pid/fd | wc -l
```

### 1.9 Linux 系统层

```bash
# 系统版本
cat /etc/os-release

# 内核 / 架构
uname -a

# CPU / 内存概览
lscpu | head
free -h

# 网络
ip addr show
curl -4 ifconfig.me   # 公网 IP

# 防火墙（Alibaba Cloud Linux / CentOS）
systemctl status firewalld
firewall-cmd --list-all
firewall-cmd --permanent --add-port=8080/tcp
firewall-cmd --reload

# 防火墙（Ubuntu）
ufw status
ufw allow 8080/tcp

# 查找进程
ps aux | grep python
pgrep -fla mcbg

# 杀进程
kill <PID>          # 温和
kill -9 <PID>       # 强制

# 系统负载 / top
top                 # q 退出
htop                # 更好看，可能要装

# 定时任务
crontab -l          # 当前用户的 cron
cat /etc/cron.hourly/mcbg-cleanup
```

### 1.10 用户 / 权限

```bash
# 看 mcbg 用户
id mcbg
getent passwd mcbg

# 查目录权限
ls -ld /opt/mcbg-data-export /var/lib/mcbg-export

# 修所有者
chown -R mcbg:mcbg /var/lib/mcbg-export
```

---

## 二、本地开发 / 打包（Windows）

### 2.1 扩展打包 / 分发

```powershell
# 打包扩展成 zip，发给同事
powershell -ExecutionPolicy Bypass -File D:\ai_code\mcbg-data-export\scripts\pack_extension.ps1

# 产物会生成在项目根目录：mcbg-extension-v<version>.zip

# bump 版本后重新打包
# 1. 编辑 extension\manifest.json 的 "version"
# 2. 再跑上面的脚本
```

### 2.2 本地启动服务端

```powershell
# PowerShell
cd D:\ai_code\mcbg-data-export\server

# 首次安装依赖
pip install -r requirements.txt

# 启动（默认 127.0.0.1:19527）
python run.py

# 指定导出目录到大盘，避免 C 盘炸
$env:MCBG_EXPORT_DIR = "D:\mcbg-export"
python run.py

# 停止：Ctrl+C
```

### 2.3 Windows 上的 Python 环境

```powershell
# 版本
python --version
python -m pip --version

# 装 / 升级 Pillow（图标脚本用）
python -m pip install --user Pillow

# 清 pip 缓存
python -m pip cache purge

# 建 venv（推荐本地开发用）
cd D:\ai_code\mcbg-data-export\server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 退出 venv
deactivate
```

### 2.4 本地 health check / curl

```powershell
# PowerShell
Invoke-WebRequest http://127.0.0.1:19527/api/health | Select-Object -ExpandProperty Content

# 或用 curl（Windows 10+ 自带）
curl http://127.0.0.1:19527/api/health

# 测远端 ECS 端口通不通
Test-NetConnection -ComputerName <ECS公网IP> -Port 8080
```

### 2.5 SSH / SCP 到 ECS

```powershell
# 登录 ECS
ssh root@<ECS公网IP>

# 指定密钥
ssh -i C:\Users\Administrator\.ssh\id_ed25519 root@<ECS公网IP>

# 复制代码到 ECS
scp -r D:\ai_code\mcbg-data-export\server\app root@<ECS公网IP>:/root/mcbg-data-export/server/
scp D:\ai_code\mcbg-data-export\server\requirements.txt root@<ECS公网IP>:/root/mcbg-data-export/server/

# 从 ECS 拉日志回本地
scp root@<ECS公网IP>:/var/log/messages D:\tmp\
```

### 2.6 图标 / 资源重新生成

```powershell
# 从 icon_source.png 重出三个尺寸
cd D:\ai_code\mcbg-data-export\extension\icons
python -c "from PIL import Image; im=Image.open('icon_source.png'); [im.resize((s,s), Image.LANCZOS).save(f'icon{s}.png',optimize=True) for s in (16,48,128)]"
```

---

## 三、Chrome 扩展端

### 3.1 加载 / 刷新 / 移除

- 地址栏打开 `chrome://extensions/`
- 顶部「开发者模式」必须开
- **加载**：点「加载已解压的扩展程序」→ 选 `extension/` 目录
- **刷新**（代码改了后）：扩展卡片上的刷新 ↻ 按钮
- **移除**：扩展卡片上的「移除」按钮
- **禁用 / 启用**：卡片右下角开关

### 3.2 调试

- 侧边栏 UI：右键 → 「检查」，打开 DevTools
- service worker：`chrome://extensions/` → 扩展卡片 →「检查视图 service worker」
- 清配置：扩展卡片 → 「详细信息」 → 下拉 → 清除数据；或 DevTools Console 里 `chrome.storage.local.clear()`

---

## 四、常见排障速查表

| 症状 | 第一步查什么 |
|---|---|
| 浏览器无法访问 | `systemctl status mcbg-data-export` + `ss -tlnp \| grep 8080` |
| 服务 active 但接口超时 | ECS 安全组 8080 入方向、`firewall-cmd --list-ports` |
| 服务起不来 | `journalctl -u mcbg-data-export -n 100` |
| ModuleNotFoundError | `$VENV/bin/pip list`，依赖没装或 venv Python 版本错 |
| 导出大文件失败 | `df -h /`、`free -h`，多半是磁盘满或 OOM |
| xlsx 巨慢 | 改导 CSV，走 pyarrow native 快一个量级 |
| 扩展按钮无响应 | `chrome://extensions/` 看有没有「错误」红字 |

---

## 五、一条命令健康体检

SSH 上 ECS 执行，一次看完关键状态：

```bash
echo "== service ==" && systemctl is-active mcbg-data-export; \
echo "== port ==" && ss -tlnp | grep 8080; \
echo "== health ==" && curl -s http://127.0.0.1:8080/api/health; echo; \
echo "== disk ==" && df -h / | tail -1; \
echo "== export dir ==" && du -sh /var/lib/mcbg-export/ 2>/dev/null; \
echo "== memory ==" && free -h | head -2; \
echo "== recent errors ==" && journalctl -u mcbg-data-export --since "1 hour ago" -p err --no-pager | tail
```
