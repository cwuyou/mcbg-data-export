#!/usr/bin/env bash
# MCBG Data Export - ECS 部署脚本
# 系统: Alibaba Cloud Linux 3 / CentOS 7+ / Ubuntu 20.04+
# 用法: 以 root 或 sudo 运行，在项目根目录执行:
#   sudo bash deploy/ecs_install.sh

set -euo pipefail

APP_DIR=/opt/mcbg-data-export
EXPORT_DIR=/var/lib/mcbg-export
USER_NAME=mcbg
SERVICE_NAME=mcbg-data-export
PORT=8080

echo "==> 检测包管理器"
if command -v apt-get >/dev/null 2>&1; then
    PKG=apt
elif command -v dnf >/dev/null 2>&1; then
    PKG=dnf
elif command -v yum >/dev/null 2>&1; then
    PKG=yum
else
    echo "不支持的系统，请手工安装 Python 3.9+ 和 git"
    exit 1
fi

echo "==> 检查磁盘空间"
FREE_GB=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
if [ "${FREE_GB:-0}" -lt 10 ]; then
    echo "错误：根分区剩余空间不足 10G (当前 ${FREE_GB}G)，无法部署"
    echo "请清理磁盘或扩容系统盘后重试"
    exit 1
fi
echo "根分区剩余: ${FREE_GB}G"

echo "==> 配置 swap (2G)：低配机器跑 xlsx 导出时兜底，避免 OOM kill"
if [ ! -f /swapfile ] && ! swapon --show | grep -q .; then
    if fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048; then
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        if ! grep -q '/swapfile' /etc/fstab; then
            echo '/swapfile none swap sw 0 0' >> /etc/fstab
        fi
        # 低配机器把 swap 用得更保守一点
        sysctl -w vm.swappiness=20 >/dev/null
        if ! grep -q '^vm.swappiness' /etc/sysctl.conf; then
            echo 'vm.swappiness=20' >> /etc/sysctl.conf
        fi
        echo "swap 已创建: 2G"
    else
        echo "警告：swap 创建失败，跳过（磁盘空间或权限不足）"
    fi
else
    echo "已有 swap，跳过"
fi

echo "==> 安装依赖"
case "$PKG" in
    apt)
        apt-get update
        apt-get install -y python3 python3-venv python3-pip git rsync
        ;;
    dnf|yum)
        $PKG install -y python3 python3-pip git rsync
        ;;
esac

echo "==> 选择 Python 解释器 (需要 3.8+)"
PY_BIN=""
# 按优先级找可用的 python3.x
for candidate in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")
        major=${ver%%.*}
        minor=${ver##*.}
        if [ "$major" = "3" ] && [ "${minor:-0}" -ge 8 ]; then
            PY_BIN=$(command -v "$candidate")
            echo "找到 Python: $PY_BIN ($ver)"
            break
        fi
    fi
done

if [ -z "$PY_BIN" ]; then
    echo "系统 Python 版本太旧 (fastapi>=0.110 需要 Python 3.8+)，尝试安装新版..."
    case "$PKG" in
        apt)
            apt-get install -y python3.11 python3.11-venv python3.11-dev 2>/dev/null \
                || apt-get install -y python3.10 python3.10-venv python3.10-dev 2>/dev/null \
                || apt-get install -y python3.9 python3.9-venv python3.9-dev
            ;;
        dnf)
            $PKG install -y python3.11 python3.11-pip 2>/dev/null \
                || $PKG install -y python3.9 python3.9-pip 2>/dev/null \
                || { $PKG install -y epel-release && $PKG install -y python39 python39-pip; }
            ;;
        yum)
            yum install -y epel-release
            yum install -y python39 python39-pip
            ;;
    esac
    # 再找一次
    for candidate in python3.12 python3.11 python3.10 python3.9 python3.8; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PY_BIN=$(command -v "$candidate")
            echo "已安装: $PY_BIN"
            break
        fi
    done
fi

if [ -z "$PY_BIN" ]; then
    echo "错误：未找到 Python 3.8+，且自动安装失败"
    echo "请手工安装后重试 (推荐 python3.11 或 python3.9)"
    exit 1
fi

echo "==> 创建系统用户 $USER_NAME（若不存在）"
if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd -r -s /sbin/nologin -d "$APP_DIR" "$USER_NAME"
fi

echo "==> 同步代码到 $APP_DIR"
mkdir -p "$APP_DIR"
# 假设当前目录就是项目根，拷贝 server/ 过去
rsync -a --delete --exclude '__pycache__' --exclude '.venv' \
    "$(pwd)/server/" "$APP_DIR/server/"

echo "==> 创建导出目录 $EXPORT_DIR"
mkdir -p "$EXPORT_DIR"
chown -R "$USER_NAME:$USER_NAME" "$EXPORT_DIR" "$APP_DIR"

echo "==> 创建 venv + 安装 Python 依赖（使用阿里云 pip 镜像加速）"
sudo -u "$USER_NAME" "$PY_BIN" -m venv "$APP_DIR/server/.venv"
VENV_PY="$APP_DIR/server/.venv/bin/python"
VENV_PIP="$APP_DIR/server/.venv/bin/pip"

# 先确认 venv python 版本 >= 3.8，否则装依赖也装不上
VENV_VER=$("$VENV_PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "venv python: $VENV_VER"
VENV_MINOR=${VENV_VER##*.}
if [ "${VENV_MINOR:-0}" -lt 8 ]; then
    echo "错误：venv 里的 Python 版本是 $VENV_VER，低于 3.8，fastapi/pyarrow 无法安装"
    echo "请删除 $APP_DIR/server/.venv 后重跑脚本"
    exit 1
fi

sudo -u "$USER_NAME" "$VENV_PIP" install --upgrade pip \
    -i https://mirrors.aliyun.com/pypi/simple/
sudo -u "$USER_NAME" "$VENV_PIP" install \
    -r "$APP_DIR/server/requirements.txt" \
    -i https://mirrors.aliyun.com/pypi/simple/

# 校验关键依赖真的装上了，避免 systemd 起个空 venv 的服务
if ! sudo -u "$USER_NAME" "$VENV_PY" -c 'import uvicorn, fastapi, odps, xlsxwriter, pyarrow' 2>/dev/null; then
    echo "错误：关键依赖没装上，请检查上面 pip install 的输出"
    sudo -u "$USER_NAME" "$VENV_PIP" list
    exit 1
fi
echo "依赖校验通过"

echo "==> 安装 systemd service"
cp "$(pwd)/deploy/mcbg-data-export.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl status "$SERVICE_NAME" --no-pager || true

echo "==> 安装导出目录定时清理 (每小时清理 3 小时前的文件)"
# 服务端代码只在启动时清理 24h 旧文件，低配机器需要更激进清理防止撑爆磁盘
cat > /etc/cron.hourly/mcbg-cleanup <<'CRON_EOF'
#!/bin/bash
# mcbg-data-export: 清理 3 小时前的导出文件
find /var/lib/mcbg-export -type f -mmin +180 -delete 2>/dev/null
CRON_EOF
chmod +x /etc/cron.hourly/mcbg-cleanup

echo
echo "==> 完成"
echo "服务地址: http://<ECS公网IP>:$PORT"
echo "健康检查: curl http://127.0.0.1:$PORT/api/health"
echo
echo "[!] 下一步必做:"
echo "    1. 阿里云控制台 → ECS 安全组 → 入方向放行 TCP $PORT (建议限制源IP)"
echo "    2. ECS 要和 MaxCompute project 同 region (杭州) 才能走内网 endpoint"
echo "    3. 告诉扩展用户把'服务地址'改为 http://<ECS公网IP>:$PORT"
echo "    4. MaxCompute endpoint 建议填内网地址:"
echo "       http://service.cn-hangzhou.maxcompute.aliyun-inc.com/api"
