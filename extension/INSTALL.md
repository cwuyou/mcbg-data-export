# MCBG Data Export 扩展安装说明

MaxCompute 数据一键导出为 xlsx / csv 的 Chrome 扩展。

## 1. 解压到固定目录

解压本压缩包，把 `extension` 文件夹放到一个**不会移动不会删除**的目录。建议：

```
C:\Users\<你>\chrome-extensions\mcbg-data-export\
```

⚠ **不要**放在桌面、下载、临时目录，Chrome 每次启动都要读这个目录。一旦删除/移动，扩展立刻失效。

## 2. 加载扩展

1. Chrome 地址栏输入 `chrome://extensions/` 回车
2. 右上角打开「**开发者模式**」开关
3. 左上角点「**加载已解压的扩展程序**」
4. 选择刚才解压的 `extension` 目录（里面能看到 `manifest.json`）

成功后列表里会出现 **MCBG Data Export**。

## 3. 把扩展钉到工具栏

1. 点 Chrome 右上角的拼图图标（扩展管理）
2. 找到 MCBG Data Export → 点图钉图标把它钉住

## 4. 填配置

1. 点工具栏上的扩展图标 → 右上角齿轮图标进入配置页
2. 填 **服务地址**（问管理员要，格式 `http://<IP>:8080`）
3. 点「**测试连接**」，出现 ✓ 即服务端可达
4. 新增一个 Profile：
   - 名称：随意，比如 `生产` / `开发`
   - Endpoint：`http://service.cn-hangzhou.maxcompute.aliyun.com/api`
   - Project：你的 MaxCompute 项目名
   - AccessKey ID / Secret：你自己的 AK/SK（阿里云控制台→ AccessKey 管理）
5. 点「**保存**」

## 5. 开始导数

点扩展图标 → 侧边栏出现：

- **表 + 字段** 模式：输入表名 → 加载 schema → 勾字段 → 选分区 → 选格式 → 导出
- **自定义 SQL** 模式：粘贴 SQL → 选 CSV → 点导出（CSV 直传速度最快）

**格式建议**：
- 字段里有大 JSON / 长文本 → **强烈推荐 CSV**
- 一般报表场景（中小字段）→ xlsx 或 csv 都行
- 百万级数据 → 优先 csv

## 常见问题

**Q：点测试连接失败**
- 服务地址是否填对（开头 `http://`，含端口 `:8080`）
- 问管理员服务是否正常（让他 `systemctl status mcbg-data-export`）
- 你办公网能 ping 通那台服务器吗（不能的话让管理员开 8080 端口白名单）

**Q：AccessKey 安全吗**
- AK/SK 存在本机浏览器，用 XOR 混淆（**不是加密**）
- 请勿在公共电脑 / 多人共用的 Chrome profile 里保存生产环境 AK/SK
- 建议用权限更小的 RAM 子账号 AK，只给 MaxCompute 读权限

**Q：xlsx 超过 100 万行了**
- 自动拆成 Sheet1 / Sheet2 / ...，不会丢数据
- 单 sheet 上限是 Excel 规范，不是本工具限制

**Q：导出很慢**
- 同一时刻多人下载会抢服务器带宽，错开时间
- 大数据量一定选 CSV，xlsx 对长字段有 3~10 倍的性能劣势

## 更新扩展

管理员发新版压缩包后：
1. 解压覆盖到原目录
2. Chrome `chrome://extensions/` 页面点扩展卡片上的「**刷新**」按钮
3. 不需要重新配置，Profile 会保留
