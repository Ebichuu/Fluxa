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
MCC_PRESERVED_CORE_API_ENABLED=false
MCC_INTEGRATION_PROBE_ENABLED=false
MCC_INTEGRATION_MANAGEMENT_ENABLED=false
MCC_TELEGRAM_MANAGEMENT_ENABLED=false
MCC_HDHIVE_MANAGEMENT_ENABLED=false
MCC_CLOUD_SEARCH_ENABLED=false
MCC_CLOUD_TRANSFER_ENABLED=false
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
7. 如需检查 115、Telegram、HDHive 等连接，只开启 `MCC_INTEGRATION_PROBE_ENABLED`，不同时开启管理和转存。
8. 用户指定单条网盘测试后，先开启 `MCC_CLOUD_SEARCH_ENABLED` 验证脱敏候选，再单独开启 `MCC_CLOUD_TRANSFER_ENABLED` 执行一次转存。
9. 自动云盘兜底和后台执行器继续关闭。

## 9. 2026-07-18 本地候选镜像记录

- 源提交：`bde3eba`。
- 镜像：`media-control-center:v2-pt-rc-bde3eba`。
- 本地镜像 ID：`sha256:8b089f484bfb7d214fb1ccec5011c982a2f7f49942956d5ec1eda5095673b35d`。
- 镜像大小：77,870,075 字节。
- React 资源：`index-BpQwAyy7.css`、`index-Bgf3Xiyl.js`，认证后均返回 200。
- `/healthz` 返回 200；未登录根页面返回 401；登录返回 303，认证会话接口返回 200。
- `/api/status` 返回 200；订阅执行和 Torra 推送均被写闸门以 403 拒绝。
- `/api/115/check` 在保留核心接口总开关关闭时返回 503。
- 运行时只有一个 Gunicorn 主进程和一个 gthread worker，容器内没有 Node/npm。
- 容器重启后健康恢复，原登录会话仍可验证。

以上仅为本机隔离验收，不代表 fnOS、Torra、qB、115、Symedia 或 Emby 的真实链路已经验证。临时容器和测试目录已清理，只保留候选镜像。

## 10. SQLite 与 Torra 追更洗版实施前置条件

当前候选镜像 `media-control-center:v2-pt-rc-bde3eba` 仍使用 JSON 订阅台账，不是 SQLite/Torra 追更洗版版本。进入 fnOS 实机窗口前必须完成以下代码与本地演练：

1. 备份 `discover_subscription_items.json` 和 `discover_subscriptions.json`。
2. 在临时 SQLite 中导入并校验配置、条目数量、订阅 key、TMDB ID、媒体类型和季号。
3. 生成差异报告；存在阻塞差异时停止切换。
4. 迁移成功后只写 `db/media_control_center.sqlite3`，不双写 JSON。
5. Torra 追更洗版、订阅调度和全部外部写闸门继续默认关闭。
6. 私人 RSS 收集器使用独立 `MCC_PRIVATE_RSS_ENABLED=false` 闸门；本地测试只使用脱敏 RSS 夹具，不连接真实 Passkey 地址。
7. 本地模拟验证 RSS 去重、7 天保留、FTS5 搜索、季集匹配、每条订阅 24/48 小时窗口、RSS 即时唤醒、12/24 或 12/24/48 兜底、到期停止、下一集重开、不补扫历史订阅、幂等、冷却和崩溃续查。
8. 构建新的候选镜像并重复登录、静态资源、只读 API、写闸门、无 Node 运行层和重启验收。

fnOS 首次部署新镜像时只执行迁移预检和只读状态检查。真实 Torra 追更洗版分析/候选下载必须在用户明确进入实机窗口后，先人工验证一次与 Torra“选中分数更高”操作等价的单条动作，再开放自动追更洗版。

私人 RSS 地址、下载 URL、SQLite、WAL 和备份按用户选择包含明文 Passkey。fnOS 持久目录和备份必须视为敏感数据，只允许管理员和容器运行用户读取。
