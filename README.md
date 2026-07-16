# 媒体控制中心 v2

面向 fnOS / NAS 的个人影音中控。生产环境使用一个 Python / Flask / Gunicorn 后端，同时提供 React 页面、Mineradio 影院大厅、订阅中枢和外部服务聚合。

## 当前状态

- 最新代码统一保存在 `D:\Projects\媒体控制中心v2`。
- Python 是唯一生产后端；Node.js 只用于 React / Vite 构建。
- NasEmby Python 源码负责订阅、发现、日历、资源规则和调度，只使用一份订阅台账。
- PT / Torra 是默认获取主通道；旧的“资源优先”默认配置会安全迁移到 Torra，自动云盘兜底保持关闭。
- 网盘订阅能力已经进入统一页面和 `/api/v2`：包含全局/订阅级开关、115、Telegram、HDHive / pansou 状态与管理入口、脱敏候选预览、单条转存复查、幂等、冷却和任务中心支线状态。
- 原 NasEmby 核心接口和调用关系已经恢复；生产默认禁用，但源码、模拟测试入口和原页面参考快照全部保留。
- 影院大厅、顶部导航、媒体队列和 Mineradio 原视觉保持不变。
- fnOS 与真实订阅测试暂缓；网盘搜索、转存、登录、签到和自动兜底均由默认关闭的细分闸门保护。

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
python -m unittest discover -s services/nasemby-core/tests -v  # 当前 83 项
npm test
npm run build
docker compose config --services
docker build -t media-control-center:v2 .
```

自动测试使用临时目录和模拟客户端，不连接真实服务执行写操作。

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
