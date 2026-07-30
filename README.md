# Fluxa

当前发布版本：`v0.4.4`。

面向 fnOS / NAS 的个人影音中控。生产环境使用一个 Python / Flask / Gunicorn 后端，同时提供 React 页面、Mineradio 影院大厅、订阅中枢和外部服务聚合。

## 主要功能

- 内容发现、媒体搜索、订阅管理和播出日历。
- 顶栏"搜索媒体"全局作品搜索：一处查看追更、下载、入库和播放状态，聚合本地追更、任务、日历、已识别 RSS 和 Emby 证据，支持作品名或 `tv:202` 直达；点击结果进入作品总览页（`/media/movie/:tmdbId`、`/media/tv/:tmdbId`），按追更、Torra、下载、115、入库、Emby 六个阶段展示完整生命周期，并提供追更、任务、日历深链。
- 首页只展示今日是否正常、入库、下载中、待处理和真实异常；缺少证据时不显示绿色正常。
- 第一阶段追更工作台统一展示本地写入、Torra、镜像同步、RSS 和定时任务状态；Torra 只读订阅可直接进入追更与日历，Fluxa 本地台账仍保持独立边界。
- Torra 已有订阅支持只读对账、预览、确认导入和状态同步；第一阶段不修改或删除 Torra 订阅，也不自动创建第二套本地真相。
- Torra → qBittorrent → 115 → Symedia → Emby 的 PT 任务链观察，同时区分媒体身份、执行状态和用户介入优先级；Torra 已确认文件名与 Symedia 记录完全一致时可只读串链，没有 Torra 逐文件秒传证据时，不会由 qB 完成时间推断秒传中、已进入 115 或疑似阻塞。
- 私人 PT RSS 种子库、本地全文搜索、订阅目标精确筛选、TMDB/IMDb 身份与有界历史回填；界面可区分回填尚未运行与运行后仍未识别。
- RSS 候选按 Torra 订阅和季集跨批次分组，使用只读规则快照展示当前版本基线、最佳候选与评分明细；基线身份、版本或范围不明确时固定显示“暂未确认”，不会触发自动下载。高分冠军可执行订阅级精准下载只读预检；当前 Torra 没有订阅绑定的指定 RSS 资源入口，因此预检明确阻断，不使用通用下载器入口绕过订阅语义。
- 追更卡片按明确 TMDB 身份补齐空缺海报；无身份或仅 Torra 条目保持安全占位和只读边界。
- 手机月历保留作品海报、状态和数量，控制室、追更与任务工具栏在 390px 窄屏下保持可读且无页面级横向溢出。
- 管理端业务文字建立 11px 最低基线，错误、空状态、设置说明和任务原因提升到 12~13px，并保持桌面与手机端信息密度一致。
- 按电影或季集隔离的质量观察、人工追更分析和候选下载；可选缺集 PT 搜索兜底只消费已关联日历的明确已播缺集，按单个 Torra 订阅排队且全局只运行一个 Torra 动作，不自动下载。
- 质量观察生产桥接支持关闭、影子和正式三态；首次影子水位永久保留，新 qB 完成与 Symedia 入库分别创建或推进观察单元。已有资源通过最多 200 集的持久预览、明确确认和原子历史基线初始化接入，真实历史时间过期的记录不会重新获得观察窗口。
- MoviePilot 人工备用入口，以及 Emby、qB、Torra、Symedia 服务状态；控制室只读展示 Torra 正式批次/调度证据，Torra 未提供订阅级搜索模式时明确显示“暂未确认”，RSS 优先调整只生成阻断预览，不修改 Torra；Symedia 明确区分历史接口可读与归档监控、STRM 等尚未接入的子能力，并提供保守洗版摘要。
- 深色/浅色工作台与独立 Mineradio 影院大厅。

## Docker Compose 快速部署

先复制 `.env.example` 为 `.env`，再创建以下 `docker-compose.yml`。配置中的注释可以原样保留：

```yaml
# Compose 项目名称
name: fluxa

services:
  fluxa:
    # Fluxa 稳定镜像，版本由 latest 指向当前发布版本
    image: ghcr.io/ebichuu/fluxa:latest
    container_name: fluxa
    restart: unless-stopped

    ports:
      # 宿主机端口:容器端口；需要改端口时只修改左侧
      - "8987:8987"

    # 首次启动可用 .env 提供初始值；登录后也可在设置页修改
    env_file:
      - .env

    # 固定运行参数，不需要在 .env 中重复填写
    environment:
      MCC_ENV: production
      APP_PORT: "8987"

    volumes:
      # 配置与活动记录
      - ${MCC_DATA_ROOT:-./runtime}/data:/app/data
      # SQLite 台账、RSS 索引与缓存
      - ${MCC_DATA_ROOT:-./runtime}/db:/app/db
      # 上传文件与运行时资产
      - ${MCC_DATA_ROOT:-./runtime}/upload:/app/upload

    healthcheck:
      # 容器内部健康检查
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8987/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 20s
```

`.env` 至少填写持久目录；服务地址、账号、Token 和功能开关也可以在首次启动后登录设置页修改：

```env
# 宿主机持久目录；默认使用当前目录下的 runtime
MCC_DATA_ROOT=./runtime
```

Emby、qBittorrent、Torra、Symedia、TMDB、MoviePilot、115、Telegram、123 云盘和功能开关都能在设置页按软件分组修改。`.env` 中的同名值只作为首次启动或恢复时的初始值；未使用的项目保持空值。启动：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

访问 `http://<服务器IP>:8987`。登录后打开“设置”，可以修改全部应用级配置。密码、Token、Cookie 和 API Key 不会回显；留空保持原值，勾选清除后才会删除。Docker 宿主机端口、卷挂载和镜像标签仍由 Compose 管理。完整更新、日志、备份和回滚说明见 [Compose 部署文档](docs/DEPLOYMENT.md)。

## 架构

```text
浏览器
  → 8987
  → Gunicorn / Flask
      ├─ 整站认证与安全边界
      ├─ React 静态页面
      ├─ Mineradio 原始视觉与数据桥接
      ├─ NasEmby 订阅、发现、日历和资源规则
      └─ Emby / qB / Torra / Symedia / 115 / Telegram / HDHive / 任务链

Node.js / Vite
  → 仅在开发和镜像构建时生成前端 dist
```

## 目录

- `src/`：React 前端。
- `services/nasemby-core/app/`：统一 Python 后端和 NasEmby 业务源码。
- `services/nasemby-core/tests/`：Python 回归测试。
- `vendor/mineradio-public/`：影院大厅原始静态资源。
- `docs/contracts/`：HTTP v1 / v2 机器契约。
- `docs/`：部署、架构、API 契约、实现来源和路线图。
- `Dockerfile`、`docker-compose.yml`：唯一正式部署入口。

`services/nasemby-core` 只是源码目录名称，不代表第二个服务或第二个容器。

## 本地开发

```powershell
npm ci
python -m pip install -r services/nasemby-core/requirements.txt
npm run dev
```

- 页面：`http://127.0.0.1:5173`
- Python API：`http://127.0.0.1:12388`

Vite 会把 `/api` 和 `/mineradio` 代理到 Python。

## 本地检查

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v  # 当前后端 620 项；v2 机器契约 71 条
npm test
npm run build
docker compose config --services
docker compose config --images
```

自动测试使用临时目录和模拟客户端，不连接真实服务执行写操作，也不会向真实活动日志追加模拟记录。
正式镜像只通过 GitHub Actions 构建并推送到 GHCR，不在本地手工推送。

## 默认可用的只读功能

以下功能不依赖任何写开关，登录后即可使用：首页结论、发现、追更列表、任务中心、日历、全局作品搜索与作品总览、控制室诊断和活动日志。下方开关只控制写入和外部动作。

## 默认写保护

`.env.example` 默认关闭以下能力，Compose 会从 `.env` 注入容器：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED=false
NASEMBY_CORE_WRITE_ENABLED=false
MCC_PRIVATE_RSS_ENABLED=false
MCC_TORRA_QUALITY_WATCH_ENABLED=false
MCC_TORRA_REWASH_DOWNLOAD_ENABLED=false
MCC_MOVIEPILOT_BACKUP_ENABLED=false
MCC_PRESERVED_CORE_API_ENABLED=false
TORRA_PUSH_ENABLED=false
MCC_INTEGRATION_PROBE_ENABLED=false
MCC_INTEGRATION_MANAGEMENT_ENABLED=false
MCC_CLOUD_SEARCH_ENABLED=false
MCC_CLOUD_TRANSFER_ENABLED=false
```

因此默认只能读取当前页面、订阅快照和服务状态，不会导入或同步 Torra 订阅、创建真实订阅、运行调度、整体开放原核心接口、推送 Torra、提交追更洗版分析、下载候选或调用 MoviePilot 人工备用动作。

## 持久目录

`MCC_DATA_ROOT` 下包含：

- `data/`：受保护配置、活动日志和动作冷却状态。
- `db/`：SQLite 唯一订阅台账、私人 RSS 索引、迁移报告和发现缓存。
- `upload/`：运行时上传与临时文件。

升级或回滚前备份整个 `MCC_DATA_ROOT`，不要手工修改 SQLite 或拼接旧订阅 JSON。

## 凭据

真实账号、密码、API Key 和 Token 只能放在未跟踪的 `.env`、持久化的 `data/user.env` 或容器环境中，不能写入源码、前端资源、镜像或文档；网页设置接口只返回是否已保存。

## 致谢

特别感谢 Mineradio 项目提供影院大厅源码与视觉基础。Fluxa 在保留其原始视觉体验的基础上完成了登录保护、媒体数据桥接与中控集成。

## 文档

- [系统框架](docs/FRAMEWORK.md)
- [API 契约](docs/API_CONTRACT.md)
- [HTTP v2 机器契约](docs/contracts/http-api-contract-v2.json)
- [核心接口能力矩阵](docs/CORE_API_CAPABILITY_MATRIX.md)
- [部署与回滚](docs/DEPLOYMENT.md)
- [实现来源](docs/IMPLEMENTATION_SOURCES.md)
- [管理员认证](docs/AUTHENTICATION.md)
- [页面地址与可分享参数](docs/URL_STATE.md)
- [前端 UI 改造实施计划](docs/Fluxa-前端UI改造实施计划.md)
- [产品设计基线](docs/PRODUCT_DESIGN.md)
- [未完成能力路线图](docs/ROADMAP.md)
