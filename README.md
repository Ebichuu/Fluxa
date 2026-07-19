# Fluxa

当前发布版本：`v0.2.2`。

面向 fnOS / NAS 的个人影音中控。生产环境使用一个 Python / Flask / Gunicorn 后端，同时提供 React 页面、Mineradio 影院大厅、订阅中枢和外部服务聚合。

## 主要功能

- 内容发现、媒体搜索、订阅管理和播出日历。
- Torra → qBittorrent → 115 → Symedia → Emby 的 PT 任务链观察。
- 私人 PT RSS 种子库、本地全文搜索和来源管理。
- 按电影或季集隔离的质量观察、人工追更分析和候选下载。
- MoviePilot 人工备用入口，以及 Emby、qB、Torra、Symedia 服务状态。
- 深色/浅色工作台与独立 Mineradio 影院大厅。

## Docker Compose 快速部署

先复制 `.env.example` 为 `.env`，再创建以下 `docker-compose.yml`。配置中的注释可以原样保留：

```yaml
# Compose 项目名称
name: fluxa

services:
  fluxa:
    # Fluxa v0.2.2 镜像
    image: ghcr.io/ebichuu/fluxa:v0.2.2
    container_name: fluxa
    restart: unless-stopped

    ports:
      # 宿主机端口:容器端口；需要改端口时只修改左侧
      - "8987:8987"

    # 账号、服务地址和功能开关统一放在 .env
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

`.env` 至少填写：

```env
# Fluxa 页面登录密钥，至少 16 个字符
MCC_ACCESS_KEY=请替换为随机长密码

# 宿主机持久目录；默认使用当前目录下的 runtime
MCC_DATA_ROOT=./runtime

# 局域网 HTTP 使用 false；HTTPS 使用 true
MCC_COOKIE_SECURE=false
```

Emby、qBittorrent、Torra、Symedia、TMDB 和 MoviePilot 等配置直接在 `.env` 中按需填写，未使用的项目保持空值。启动：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

访问 `http://<服务器IP>:8987`。完整更新、日志、备份和回滚说明见 [Compose 部署文档](docs/DEPLOYMENT.md)。

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
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v  # 当前 166 项
npm test
npm run build
docker compose config --services
docker compose config --images
```

自动测试使用临时目录和模拟客户端，不连接真实服务执行写操作，也不会向真实活动日志追加模拟记录。
正式镜像只通过 GitHub Actions 构建并推送到 GHCR，不在本地手工推送。

## 默认写保护

`.env.example` 默认关闭以下能力，Compose 会从 `.env` 注入容器：

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

## 致谢

特别感谢 Mineradio 项目提供影院大厅源码与视觉基础。Fluxa 在保留其原始视觉体验的基础上完成了登录保护、媒体数据桥接与中控集成。

## 文档

- [系统框架](docs/FRAMEWORK.md)
- [API 契约](docs/API_CONTRACT.md)
- [HTTP v2 机器契约](docs/contracts/http-api-contract-v2.json)
- [核心接口能力矩阵](docs/CORE_API_CAPABILITY_MATRIX.md)
- [部署与回滚](docs/DEPLOYMENT.md)
- [实现来源](docs/IMPLEMENTATION_SOURCES.md)
- [UI 规范](docs/UI_STANDARD.md)
- [未完成能力路线图](docs/ROADMAP.md)
