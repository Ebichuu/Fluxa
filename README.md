# 媒体控制中心 v2

面向 fnOS / NAS 的个人影音中控。生产环境使用一个 Python / Flask / Gunicorn 后端，同时提供 React 页面、Mineradio 影院大厅、订阅中枢和外部服务聚合。

## 当前状态

- 最新代码统一保存在 `D:\Projects\媒体控制中心v2`。
- Python 是唯一生产后端；Node.js 只用于 React / Vite 构建。
- NasEmby Python 源码负责订阅、发现、日历、资源规则和调度，只使用一份订阅台账。
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
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v  # 当前 85 项
npm test
npm run build
docker compose config --services
docker build -t media-control-center:v2 .
```

自动测试使用临时目录和模拟客户端，不连接真实服务执行写操作，也不会向真实活动日志追加模拟记录。

## Docker / fnOS

1. 复制 `.env.example` 为未跟踪的 `.env`。
2. 设置至少 16 字符的 `MCC_ACCESS_KEY`。
3. 将 `MCC_DATA_ROOT` 指向 fnOS 持久目录。
4. 填写需要接入的 Emby、qB、Torra、Symedia 和 TMDB 配置。
5. 启动：

```bash
docker compose up -d --build
```

访问 `http://<fnOS-IP>:8787`。公网必须使用 HTTPS 反向代理并限制源站端口。

## 默认写保护

Compose 固定关闭：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
NASEMBY_CORE_WRITE_ENABLED=false
MCC_PRESERVED_CORE_API_ENABLED=false
TORRA_PUSH_ENABLED=false
MCC_INTEGRATION_PROBE_ENABLED=false
MCC_INTEGRATION_MANAGEMENT_ENABLED=false
MCC_CLOUD_SEARCH_ENABLED=false
MCC_CLOUD_TRANSFER_ENABLED=false
```

因此默认只能读取当前页面、订阅快照和服务状态，不会创建真实订阅、运行调度、整体开放原核心接口或推送 Torra。

## 持久目录

`MCC_DATA_ROOT` 下包含：

- `data/`：受保护配置、活动日志和动作冷却状态。
- `db/`：NasEmby 订阅台账、订阅配置和发现缓存。
- `upload/`：运行时上传与临时文件。

升级或回滚前备份整个 `MCC_DATA_ROOT`，不要手工拼接订阅 JSON。

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
