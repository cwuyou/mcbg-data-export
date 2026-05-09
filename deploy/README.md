# ECS 公网部署指南

把 MCBG Data Export 服务端部署到阿里云 ECS，给多人公网使用。

## 前置条件

- 一台阿里云 ECS（**杭州 region**，与 MaxCompute project 同 region）
- 建议配置：2 vCPU / 4 GB 内存 / 100 GB 系统盘（xlsx 临时文件吃盘）
- 系统镜像：Alibaba Cloud Linux 3 / Ubuntu 20.04+ / CentOS 7+
- 公网 IP（按带宽计费 5 Mbps 起步，峰值拉满下载速度）

## ⚠ 安全说明（必读）

当前架构下，部署到公网意味着：

1. **任何知道 IP 的人都能调用服务**（你选择了不加服务端鉴权，安全性依赖 MaxCompute 自身的 AK/SK 校验）
2. **HTTP 明文传输**（没有域名无法上 Let's Encrypt 证书），路径上能抓到 AccessKey。建议：
   - 后续弄个域名（50 元/年），换 HTTPS
   - 或者只限制安全组只放行同事的公网 IP，等于"半内网"
3. **用户的 AK/SK 会经过你的 ECS**，服务器若被入侵等于所有用户 AK 泄漏
4. **没有限流**：恶意请求能消耗你的 ECS 带宽和磁盘

如果这些都能接受，继续往下。否则建议先加鉴权和域名。

## 步骤 1：ECS 基础准备

登录 ECS：

```bash
ssh root@<ECS公网IP>
```

阿里云控制台 → ECS 实例 → 安全组 → 配置规则 → **入方向添加**：
- 协议：TCP
- 端口：8080
- 源：`0.0.0.0/0`（全放开）或 `同事IP段/32`（更安全）

## 步骤 2：把代码传到 ECS

方式 A（本地 scp）：
```bash
# 本地执行
scp -r D:/ai_code/mcbg-data-export root@<ECS公网IP>:/root/
```

方式 B（ECS 从 git 拉）：如果你把代码推到 GitHub/GitLab，ECS 上 git clone。

## 步骤 3：执行部署脚本

```bash
cd /root/mcbg-data-export
sudo bash deploy/ecs_install.sh
```

脚本会：
- 装 Python3 + venv + git
- 检查磁盘剩余空间（< 10G 直接退出）
- 创建 2G swap（防止低配机器 xlsx 导出 OOM）
- 建 `mcbg` 系统用户
- 拷代码到 `/opt/mcbg-data-export/`
- 建 venv 装依赖（走阿里云 pip 镜像）
- 注册 systemd 服务并启动
- 装 hourly cron 清理 3 小时前的导出文件

完成后检查：
```bash
systemctl status mcbg-data-export
curl http://127.0.0.1:8080/api/health
```

## 步骤 4：验证公网可达

在你自己电脑上：
```bash
curl http://<ECS公网IP>:8080/api/health
# 应返回 {"ok":true,...}
```

若不通：
- 安全组规则生效了吗
- `systemctl status mcbg-data-export` 看服务起没起
- `journalctl -u mcbg-data-export -f` 看实时日志

## 步骤 5：扩展用户配置

使用者那边：

1. 打开扩展 Options 配置页
2. **服务地址**：`http://<ECS公网IP>:8080`
3. **MaxCompute endpoint**：
   - 如果用户也在阿里云内网 → `http://service.cn-hangzhou.maxcompute.aliyun-inc.com/api`（内网地址，免流量费）
   - **本次部署是 ECS 在内网调 MaxCompute**，用户端扩展本身不直接连 MaxCompute，endpoint 这里填的是**服务器用来连 MaxCompute 的地址**。由于 ECS 在杭州内网，填 `aliyun-inc.com` 那个速度最快且免流量
4. 填 AccessKey ID / Secret / Project
5. 保存、点"测试连接"→ ✓ 即通

## 运维常用命令

```bash
# 查看日志
journalctl -u mcbg-data-export -f
# 重启
systemctl restart mcbg-data-export
# 停止
systemctl stop mcbg-data-export
# 看导出目录占用
du -sh /var/lib/mcbg-export
# 手动清理
rm -rf /var/lib/mcbg-export/*
```

## 更新代码

```bash
cd /root/mcbg-data-export
git pull   # 或重新 scp
sudo rsync -a --delete --exclude '__pycache__' --exclude '.venv' \
    server/ /opt/mcbg-data-export/server/
sudo systemctl restart mcbg-data-export
```

## 后续优化建议

按优先级：

1. **加服务端 API Key 鉴权**（半小时工作量，防滥用）
2. **买个域名 + Let's Encrypt HTTPS**（一天工作量，保护 AK/SK）
3. **前置 Nginx 做限流**（防 DDoS / 防扫库）
4. **监控磁盘水位**（cron 定时清理 > 24h 文件）
