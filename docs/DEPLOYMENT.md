# fnOS 单容器部署与回滚

## 1. 部署边界

- 一个 `media-control-center` 服务。
- 一个宿主端口 `8787`。
- 一个 Python 3.13 / Gunicorn 运行时。
- Node.js 只在镜像构建阶段生成 React `dist`。
- `data/`、`db/`、`upload/` 统一持久化到 `MCC_DATA_ROOT`。

当前只准备和验证部署包，不执行 fnOS 实机安装或真实订阅。

## 2. 准备目录

在 fnOS 建立持久目录，例如：

```text
/vol1/docker/media-control-center/
  data/
  db/
  upload/
```

复制 `.env.example` 为 `.env`，至少配置：

```env
MCC_ACCESS_KEY=至少16字符的随机值
MCC_DATA_ROOT=/vol1/docker/media-control-center
MCC_ALLOWED_ORIGINS=https://你的域名
MCC_COOKIE_SECURE=true
```

按需要填写 Emby、qB、Torra、Symedia 和 TMDB 配置。`.env` 不得提交到 Git。

## 3. 默认安全开关

Compose 固定：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
NASEMBY_CORE_WRITE_ENABLED=false
TORRA_PUSH_ENABLED=false
```

代码收口和首次部署阶段不要开启这些值。

## 4. 构建与启动

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

访问：

```text
http://<fnOS-IP>:8787
```

公网必须使用 HTTPS 反向代理；8787 只允许反向代理或受信网络访问。

## 5. 只读验收

1. `/healthz` 返回 200。
2. 未登录业务 API 返回 401。
3. 使用访问密钥登录后首页与工作页可访问。
4. `/api/health` 返回 `runtime=python`。
5. 订阅列表可读取。
6. 订阅保存因写闸门返回 403。
7. 错误 Origin 的写请求返回 403。
8. 已保留的核心兼容 API 返回 `503 PRESERVED_CORE_API_DISABLED`，`/static/app.js` 返回 404。
9. 容器进程只有 Gunicorn/Python，容器内找不到 Node。
10. 重启后健康恢复，持久目录中的标记或数据仍存在。

## 6. 备份

升级或开启任何写能力前备份整个 `MCC_DATA_ROOT`：

```text
data/
db/
upload/
```

不要只备份某一个订阅 JSON，也不要手工合并两份台账。

## 7. 回滚

1. 停止当前容器。
2. 恢复上一份已验证镜像或 v2 Git 提交。
3. 保持 `MCC_DATA_ROOT` 不变。
4. 如数据已经发生写入，先恢复完整持久目录备份，再启动旧镜像。
5. 确认只有一个容器和一套调度器运行。

## 8. 以后实机写入顺序

等待用户明确进入实机窗口后：

1. 备份持久目录。
2. 只开启 `NASEMBY_CORE_WRITE_ENABLED`。
3. 从媒体控制中心创建一条测试订阅并核对唯一台账。
4. 核对分类、保存路径、Torra 查重和下载器 ID。
5. 再开启 `TORRA_PUSH_ENABLED`，只验证单条 PT / Torra 主链。
6. 完整链路稳定后最后开启订阅调度。
7. 自动云盘兜底继续关闭。
