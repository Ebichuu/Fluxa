# Fluxa Compose 部署与回滚

## 1. 部署边界

- 一个 `fluxa` 服务。
- 一个宿主端口 `8987`。
- 一个 Python 3.13 / Gunicorn 运行时。
- Node.js 只在镜像构建阶段生成 React `dist`。
- `data/`、`db/`、`upload/` 统一持久化到 `MCC_DATA_ROOT`。

当前只准备和验证部署包，不执行 fnOS 实机安装或真实订阅。

## 2. 准备目录

在 fnOS 建立持久目录，例如：

```text
/vol1/docker/fluxa/
  data/
  db/
  upload/
```

复制 `.env.example` 为 `.env`，至少配置持久目录；外部服务也可以首次启动后在网页“设置”中填写：

```env
# 宿主机持久目录，内部需要 data、db、upload 三个子目录
MCC_DATA_ROOT=/vol1/docker/fluxa
```

如果选择在启动前填写 Emby、qB、Torra、Symedia、TMDB 或其他服务配置，`.env` 仍会作为初始值；登录后网页保存的配置会写入持久化目录并覆盖同名初始值。`.env` 不得提交到 Git。

## 3. Docker Compose 配置

将以下内容保存为 `docker-compose.yml`。注释只用于说明，可以原样保留：

```yaml
# Compose 项目名，用于区分同一台机器上的其他容器项目
name: fluxa

services:
  fluxa:
    # 使用 GitHub Container Registry 发布的稳定镜像
    image: ghcr.io/ebichuu/fluxa:latest

    # 固定容器名，便于在 fnOS 或命令行中定位
    container_name: fluxa
    restart: unless-stopped

    ports:
      # 宿主机端口:容器端口；只修改左侧即可更换访问端口
      - "8987:8987"

    # 首次启动的初始值；登录后可在网页设置中修改并保存到 data/user.env
    env_file:
      - .env

    # 只有不会因用户配置改变的运行参数留在 Compose 中
    environment:
      MCC_ENV: production
      APP_PORT: "8987"

    volumes:
      # 业务配置和活动记录
      - ${MCC_DATA_ROOT:-./runtime}/data:/app/data
      # SQLite 订阅台账、RSS 索引和缓存
      - ${MCC_DATA_ROOT:-./runtime}/db:/app/db
      # 上传文件和运行时临时资产
      - ${MCC_DATA_ROOT:-./runtime}/upload:/app/upload

    healthcheck:
      # 容器内部健康检查，不依赖宿主机端口映射
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8987/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 20s
```

`docker-compose.yml`、`.env` 和持久目录可以放在同一部署目录中，例如 `/vol1/docker/fluxa/`。Compose 自动读取当前目录的 `.env` 解析 `MCC_DATA_ROOT`，并通过 `env_file` 将其余服务配置传入容器。

## 4. 默认安全开关

`.env.example` 默认关闭以下能力；复制为 `.env` 后会由 Compose 统一注入容器：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
NASEMBY_CORE_WRITE_ENABLED=false
MCC_PRIVATE_RSS_ENABLED=false
MCC_TORRA_QUALITY_WATCH_ENABLED=false
MCC_TORRA_REWASH_DOWNLOAD_ENABLED=false
MCC_MOVIEPILOT_BACKUP_ENABLED=false
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

## 5. 首次启动

```bash
docker compose config
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 fluxa
```

Compose 会从 `.env` 读取全部服务配置，并拉取 `ghcr.io/ebichuu/fluxa:latest`。正式镜像统一由 GitHub Actions 构建并推送到 GHCR；每次发布先推送不可变版本/SHA 镜像，容器冒烟通过后才更新 `latest`，无需修改 Compose 版本号。

访问：

```text
http://<fnOS-IP>:8987
```

首次打开会进入管理员初始化页面，创建账号和密码即可。忘记密码时在部署目录执行：

```bash
docker compose exec fluxa python -m app.admin reset-password
```

登录后进入“设置”即可编辑 Emby、qBittorrent、Torra、Symedia、TMDB、MoviePilot、115、Telegram、123 云盘、代理和全部应用开关。敏感项不会回显，留空保持原值；明确勾选清除才会删除。调度线程类开关会提示重启后生效，其余连接配置会立即刷新。

公网必须使用 HTTPS 反向代理；8987 只允许反向代理或受信网络访问。

更新镜像并重建容器：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

持续查看日志：

```bash
docker compose logs -f --tail=100 fluxa
```

停止并删除容器，但保留宿主机持久目录：

```bash
docker compose down
```

## 6. 只读验收

1. `/healthz` 返回 200。
2. 未登录业务 API 返回 401。
3. 首次打开创建管理员，之后使用账号密码登录。
4. `/api/health` 返回 `runtime=python`。
5. 订阅列表可读取。
6. 订阅保存因写闸门返回 403。
7. 通过直连或反向代理登录均不会返回 `ORIGIN_FORBIDDEN`。
8. 已保留的核心兼容 API 返回 `503 PRESERVED_CORE_API_DISABLED`，`/static/app.js` 返回 404。
9. 容器进程只有 Gunicorn/Python，容器内找不到 Node。
10. 重启后健康恢复，持久目录中的标记或数据仍存在。

## 7. 备份

升级或开启任何写能力前备份整个 `MCC_DATA_ROOT`：

```text
data/
db/
upload/
```

不要只备份某一个订阅 JSON，也不要手工合并两份台账。

## 8. 回滚

1. 停止当前容器。
2. 恢复上一份已验证镜像或 v2 Git 提交。
3. 保持 `MCC_DATA_ROOT` 不变。
4. 如数据已经发生写入，先恢复完整持久目录备份，再启动旧镜像。
5. 确认只有一个容器和一套调度器运行。

## 9. 以后实机写入顺序

等待用户明确进入实机窗口后：

1. 备份持久目录。
2. 只开启 `NASEMBY_CORE_WRITE_ENABLED`。
3. 从媒体控制中心创建一条测试订阅并核对唯一台账。
4. 核对分类、保存路径、Torra 查重和下载器 ID。
5. 再开启 `TORRA_PUSH_ENABLED`，只验证单条 PT / Torra 主链。
6. 完整链路稳定后最后开启订阅调度。
7. 如需检查 115、Telegram、HDHive 等连接，只开启 `MCC_INTEGRATION_PROBE_ENABLED`，不同时开启管理和转存。
8. 用户指定单条网盘测试后，先开启 `MCC_CLOUD_SEARCH_ENABLED` 验证脱敏候选，再单独开启 `MCC_CLOUD_TRANSFER_ENABLED` 执行一次转存。
9. MoviePilot 仅在相关观察窗口全部到期、Torra/qB 预检通过并另行批准单条动作后，临时开启 `MCC_MOVIEPILOT_BACKUP_ENABLED`；不得与 Torra 并行下载。
10. 自动云盘兜底、MoviePilot 自动调度和后台执行器继续关闭。

## 10. 2026-07-18 本地候选镜像记录

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

## 11. SQLite 首次部署与 Torra 追更洗版前置条件

旧候选镜像 `media-control-center:v2-pt-rc-bde3eba` 使用 JSON 订阅台账。2026-07-18 已完成 SQLite/RSS 第一版候选 `media-control-center:sqlite-rss-preview`，并在收集器和迁移硬化后重建 `media-control-center:sqlite-rss-hardened`。用户已确认 fnOS 没有需要保留的旧订阅或配置数据，本次首次部署使用空 SQLite，不执行真实 JSON 差异迁移：

1. 为 `MCC_DATA_ROOT` 准备空的、可写的持久目录，并在启动前备份该目录的初始状态。
2. 首次启动由当前源码直接创建 `db/media_control_center.sqlite3` schema version 4；确认 WAL、FTS5、资源事件表和健康状态正常。
3. 启动后只写 SQLite，不创建或双写旧订阅 JSON。
4. 如果以后确实出现需要保留的旧 JSON，再使用已经通过模拟演练的临时 SQLite 原子迁移、逐字段校验和差异报告流程；迁移失败不得发布临时库。
5. Torra 追更洗版、订阅调度和全部外部写闸门继续默认关闭。
6. 私人 RSS 收集器使用独立 `MCC_PRIVATE_RSS_ENABLED=false` 闸门；本地测试只使用脱敏 RSS 夹具，不连接真实 Passkey 地址。
7. 本地模拟验证 RSS 去重、7 天保留、FTS5 搜索、季集匹配、每条订阅 24/48 小时窗口、RSS 即时唤醒、12/24 或 12/24/48 兜底、到期停止、下一集重开、不补扫历史订阅、幂等、冷却和崩溃续查。
8. 收集器硬化、原子迁移模拟演练和硬化候选镜像本地验收已经完成；fnOS 仍按同一清单重复只读验收。

fnOS 首次部署新镜像时只执行空库初始化和只读状态检查。真实 Torra 追更洗版分析/候选下载必须在用户明确进入实机窗口后，先人工验证一次与 Torra“选中分数更高”操作等价的单条动作，再开放自动追更洗版。

私人 RSS 地址、下载 URL、SQLite、WAL 和备份按用户选择包含明文 Passkey。fnOS 持久目录和备份必须视为敏感数据，只允许管理员和容器运行用户读取。

### SQLite/RSS 第一版候选验收记录

- 镜像：`media-control-center:sqlite-rss-preview`。
- 本地镜像 ID：`sha256:51844ad12598e1453fae47fb65bfcd3c88665ee347e3669df797e21dd1b92157`。
- 镜像大小：78,228,632 字节。
- 98 项 Python 回归、前端生产构建、npm 高危审计和 Compose 配置通过。
- 临时容器完成登录、RSS 来源本地写入、API 脱敏读取和重启持久化。
- 容器内 SQLite 为 WAL 模式且 FTS5 可用；运行层只有 Python / Gunicorn，没有 Node/npm。
- `MCC_PRIVATE_RSS_ENABLED=false`，没有连接真实 RSS、Torra、qB、MoviePilot、115、Symedia 或 Emby 写接口。
- 临时容器和验收目录已清理，只保留候选镜像。

### SQLite/RSS 硬化候选验收记录

- 镜像：`media-control-center:sqlite-rss-hardened`。
- 本地镜像 ID：`sha256:7760049b676f6200a3529d156da0b4ba0a46a3fea48b8c66c8fd6ece3fb909d3`。
- 镜像大小：78,239,588 字节。
- 102 项 Python 回归、前端类型检查和生产构建、npm 高危审计、Compose 配置、安全与质量扫描通过。
- 临时容器完成登录、RSS 来源本地写入、`MCC_PRIVATE_RSS_ENABLED=false` 的 503 闸门、API/日志脱敏和容器重建持久化。
- SQLite 为 WAL、schema version 2，FTS5 可用；运行层没有 Node/npm。
- 原子迁移模拟演练确认共享 RSS 表保留，差异失败不发布半成品，迁移报告不包含测试凭据。
- 没有连接真实 RSS、Torra、qB、MoviePilot、115、Symedia 或 Emby 写接口；临时容器和验收目录已清理。

### 当前源码验收记录

- 当前源码使用 SQLite schema version 4，包含质量观察、provider 动作、调度状态、私人 RSS 种子索引和资源事件账本。
- 285 项 Python 回归、53 条 v2 机器契约、前端类型检查和生产构建通过；资源身份/执行状态、正常保护一致性、用户/技术原因分层、首页季集定位、事件幂等、qB 动作预览、日历未知/逾期判定、RSS 精确搜索与身份回填、追更海报补齐均使用临时台账和脱敏夹具验证，真实 Torra/RSS 写动作保持关闭。
- Torra 分析、下载和 job 查询测试全部使用假 session；质量观察与调度使用假任务链证据、假 Torra/qB 客户端和临时 SQLite，覆盖双闸门、并发 1、批量 2、公平游标、限额、截止点与租约恢复，没有连接真实外部服务。
- 阶段 6 的 GET/PATCH/POST 契约、202 + Location、错误状态映射、跨匹配幂等冲突和独立下载闸门已通过模拟 API 测试；候选下载不会因分析闸门开启而自动执行。
- 正式 GHCR 镜像只由 GitHub Actions 构建；版本标签和 SHA 镜像保持不可变，容器冒烟成功后才将同一 digest 提升为 `latest`。部署后仍需重复只读、重启和 schema 验收。
