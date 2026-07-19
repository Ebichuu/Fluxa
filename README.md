# Fluxa

当前发布版本：`v0.2`。

面向 fnOS / NAS 的个人影音中控。生产环境使用一个 Python / Flask / Gunicorn 后端，同时提供 React 页面、Mineradio 影院大厅、订阅中枢和外部服务聚合。

## 当前状态

- 最新代码统一保存在 `D:\Projects\媒体控制中心v2`。
- Python 是唯一生产后端；Node.js 只用于 React / Vite 构建。
- NasEmby Python 源码负责订阅、发现、日历、资源规则和调度，只使用一份订阅台账。
- 订阅配置和条目已经切换到 `db/media_control_center.sqlite3`；旧 JSON 只作为一次性迁移和回滚输入，不再双写。
- “种子库”第一版已经落地：支持私人 PT RSS 来源配置、本地 FTS5 搜索、保留期清理和脱敏展示；真实收集默认关闭。
- RSS 收集器已补齐 `429/Retry-After`、指数退避、全局并发 2、同来源互斥和抓取记录上限；四个真实结构脱敏夹具已满足当前版本，新增站点兼容作为后续非阻塞扩展。
- fnOS 当前没有需要保留的旧订阅或配置数据，首次部署直接初始化空 SQLite；旧 JSON 原子迁移和差异报告能力继续保留，但不再是本次上线前置条件。
- 当前源码的 SQLite schema version 已升至 3，新增按电影/季集隔离的质量观察、外部动作租约和调度游标；旧硬化镜像仍停留在 schema v2。
- Torra 追更洗版阶段 1–6 已完成：分析、下载和 job 查询严格使用已核对契约；任务链与 Emby 双证据启动按集固定窗口；可靠 RSS 可即时唤醒，有限主动兜底按 SQLite 时间表、公平游标、全局并发 1、冷却和限额运行；人工设置、状态、分析和下载 API 已注册，重启后只续查原 job，自动路径不下载候选。
- MoviePilot 人工备用阶段 7 已完成：仅在相关观察单元全部到期且 Torra/qB 空闲时允许预览或确认推送；已有订阅重搜、新订阅创建、SQLite 幂等审计和响应脱敏均由独立默认关闭闸门保护，React 只提供人工入口，不含自动调度。
- SQLite/RSS 第一版候选镜像已完成登录、WAL/FTS5、无 Node 运行层和容器重启持久化验收。
- 硬化候选镜像 `media-control-center:sqlite-rss-hardened` 已完成 schema v2、收集闸门、脱敏和重启持久化冒烟。
- PT 主线固定为媒体控制中心订阅 → Torra → qB → Torra 秒传 115 → Symedia → Emby。
- 订阅详情提供 Torra 安全预览与人工推送；Symedia 不接收重复订阅推送。
- Telegram、HDHive / pansou、影巢和 115 分享转存已从当前 React 页面隐藏，底层源码、v2 接口和模拟测试完整保留，等待以后版本。
- 原 NasEmby 核心接口和调用关系已经恢复；生产默认禁用，但源码、模拟测试入口和原页面参考快照全部保留。
- Mineradio 影院大厅主体视觉保持不变；导航增加订阅入口，媒体抽屉改为准确的“媒体库 / 本库内容”语义并支持移动端明确开合。
- fnOS 与真实订阅测试暂缓；Torra 推送及所有外部写动作均由默认关闭的细分闸门保护。

## 架构

```text
浏览器
  → 8787
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
- `docs/contracts/`：HTTP v1 机器契约。
- `docs/references/`：只读参考资料，不参与运行。
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
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v  # 当前 165 项
npm test
npm run build
docker compose config --services
docker build -t fluxa:v0.2 .
```

自动测试使用临时目录和模拟客户端，不连接真实服务执行写操作，也不会向真实活动日志追加模拟记录。

## Docker / fnOS

GitHub Container Registry 镜像：

```bash
docker pull ghcr.io/ebichuu/fluxa:v0.2
```

`docker-compose.yml` 默认使用该版本，也可以通过 `MCC_IMAGE` 指定其他标签。

1. 复制 `.env.example` 为未跟踪的 `.env`。
2. 设置至少 16 字符的 `MCC_ACCESS_KEY`。
3. 将 `MCC_DATA_ROOT` 指向 fnOS 持久目录。
4. 填写需要接入的 Emby、qB、Torra、Symedia 和 TMDB 配置；TMDB 支持旧版 `TMDB_API_KEY` 或 v4 `TMDB_API_TOKEN`。
5. 拉取并启动：

```bash
docker compose pull
docker compose up -d
```

本地构建时可先执行 `docker build -t fluxa:v0.2 .`，再将 `.env` 中的 `MCC_IMAGE` 改为 `fluxa:v0.2`。

访问 `http://<fnOS-IP>:8787`。公网必须使用 HTTPS 反向代理并限制源站端口。

## 默认写保护

Compose 固定关闭：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
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

因此默认只能读取当前页面、订阅快照和服务状态，不会创建真实订阅、运行调度、整体开放原核心接口、推送 Torra、提交追更洗版分析、下载候选或调用 MoviePilot 人工备用动作。

## 持久目录

`MCC_DATA_ROOT` 下包含：

- `data/`：受保护配置、活动日志和动作冷却状态。
- `db/`：SQLite 唯一订阅台账、私人 RSS 索引、迁移报告和发现缓存。
- `upload/`：运行时上传与临时文件。

升级或回滚前备份整个 `MCC_DATA_ROOT`，不要手工修改 SQLite 或拼接旧订阅 JSON。

## 凭据

真实账号、密码、API Key 和 Token 只能放在未跟踪的 `.env` 或 fnOS 容器环境中，不能写入源码、前端、镜像或文档。

## 文档

- [当前计划](docs/PLAN.md)
- [系统框架](docs/FRAMEWORK.md)
- [API 契约](docs/API_CONTRACT.md)
- [HTTP v2 机器契约](docs/contracts/http-api-contract-v2.json)
- [核心接口能力矩阵](docs/CORE_API_CAPABILITY_MATRIX.md)
- [部署与回滚](docs/DEPLOYMENT.md)
- [实现来源](docs/IMPLEMENTATION_SOURCES.md)
- [UI 规范](docs/UI_STANDARD.md)
- [v2 收口与源码保留设计](docs/V2_CLEANUP_DESIGN.md)
- [v2 源码与资料完整性清单](docs/V2_SOURCE_INVENTORY.md)
- [v2 实施计划](docs/V2_IMPLEMENTATION_PLAN.md)
- [未完成能力路线图](docs/ROADMAP.md)
- [网盘订阅与获取计划](docs/CLOUD_ACQUISITION_PLAN.md)
- [PT 主链收口设计](docs/superpowers/specs/2026-07-17-pt-primary-control-center-design.md)
- [PT 主链实施计划](docs/superpowers/plans/2026-07-17-pt-primary-control-center-implementation-plan.md)
- [SQLite 与 Torra 追更洗版设计](docs/superpowers/specs/2026-07-18-sqlite-torra-quality-upgrade-design.md)
- [私人 PT RSS 种子库设计](docs/superpowers/specs/2026-07-18-private-pt-rss-seed-library-design.md)
- [SQLite 与 RSS 种子库实施计划](docs/superpowers/plans/2026-07-18-sqlite-private-rss-seed-library-implementation-plan.md)
- [Torra 追更洗版实施计划](docs/superpowers/plans/2026-07-18-torra-follow-up-rewash-implementation-plan.md)
