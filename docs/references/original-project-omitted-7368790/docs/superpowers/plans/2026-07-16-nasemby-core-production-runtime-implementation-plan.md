# NasEmby Core 生产运行时实施计划

状态：已完成
日期：2026-07-16
设计依据：`docs/superpowers/specs/2026-07-16-nasemby-core-production-runtime-design.md`

## 目标

把 NasEmby Core 的 Docker 生产入口从 Flask 开发服务器切换为 Gunicorn：固定一个 `gthread` worker、四个 HTTP 线程，并通过 worker 生命周期钩子只启动一套 HDHive、发现缓存预热和订阅任务线程。保持本地 `python -m app.main` 开发入口、业务 API、订阅数据结构和全部外部写开关不变。

## 固定边界

- 只修改 `services/nasemby-core/` 的运行时、测试和相关文档。
- 不修改 React、Express、影院大厅、媒体队列或顶部导航。
- 不拆分 scheduler 容器，不增加 Redis、队列、分布式锁或多 worker 支持。
- 不导入真实 NasEmby 数据，不调用 Torra、qB、115、Symedia、Telegram、HDHive 或 Emby 写接口。
- Docker 测试使用现有空命名卷；结束后删除测试容器与网络，保留命名卷。

## 实施步骤

### 1. 先补生产运行契约测试

修改 `services/nasemby-core/tests/test_source_contract.py`：

- 断言依赖包含 `gunicorn>=23.0,<24.0`。
- 断言 Dockerfile 使用 Gunicorn 配置和 `app.main:app`，不再以 `python -m app.main` 作为生产命令。
- 加载 `app/gunicorn.conf.py`，断言单 worker、`gthread`、四线程、内部端口、超时以及 reload/preload 关闭。
- mock `threading.Thread`，连续两次调用统一后台启动函数，断言三类调度线程各只启动一次。
- mock 统一后台启动函数并调用 `post_worker_init`，断言生命周期钩子只委托该函数。

验证：新测试在实现前失败，失败原因只指向缺失的依赖、配置和统一启动函数。

### 2. 增加最小生产运行时

修改范围：

- `requirements.txt` 增加 `gunicorn>=23.0,<24.0`。
- `app/main.py` 增加 `start_background_runtime()`，复用三个现有幂等启动函数；开发入口改为调用统一函数。
- 三个调度循环捕获异常后只记录调度器名称和异常类型，不记录载荷、凭据或请求 URL。
- 新增 `app/gunicorn.conf.py`：
  - `bind = "0.0.0.0:12388"`
  - `workers = 1`
  - `worker_class = "gthread"`
  - `threads = 4`
  - `timeout = 120`
  - `graceful_timeout = 30`
  - `keepalive = 5`
  - `reload = False`
  - `preload_app = False`
  - access/error log 输出标准输出
  - `post_worker_init` 调用 `start_background_runtime()`
- Dockerfile 的 `CMD` 改为 Gunicorn 配置加 `app.main:app`。

验证：Python 契约测试全部通过；直接导入 `app.main` 不启动后台线程；`python -m app.main` 保持开发行为。

### 3. 同步 Core 文档和补丁记录

更新：

- `services/nasemby-core/README.md`：区分 Windows/本地开发入口与 Linux Docker 生产入口。
- `services/nasemby-core/DESIGN.md`：记录单 worker 与调度所有权。
- `services/nasemby-core/patches/README.md`：记录相对 NasEmby 基线的运行时补丁、理由、兼容性和回滚。
- `docs/IMPLEMENTATION_SOURCES.md`、`docs/PLAN.md` 和本计划：记录完成状态与实测结果。

验证：文档不再声称生产容器使用 Flask 开发服务器，也不暗示可以横向扩多个 Core 副本。

### 4. 自动回归

运行：

```powershell
python -m unittest discover -s tests -v
npm test
npm run build
git diff --check
```

Python 命令在 `services/nasemby-core/` 执行。凭据扫描覆盖用户曾提供的真实地址、密码和 API Key，预期仓库无匹配。

### 5. 空数据 Docker 验收

运行：

```powershell
docker compose build --pull=false nasemby-core
docker compose up -d nasemby-core
docker compose ps
docker compose logs --no-color nasemby-core
```

检查：

- 容器达到 `healthy`。
- 进程为 Gunicorn master + 唯一 worker。
- 日志没有 Flask development server 警告，后台运行时启动日志只出现一次。
- 容器内 `GET /api/status` 返回 200。
- 容器内并发发送多次只读状态请求全部成功，无 5xx 和 worker 重启。
- `12388/tcp` 没有宿主端口映射。
- 九个外部自动动作环境变量均为 `0`。

结束后执行 `docker compose down`，不带 `-v`，保留空测试卷。

完整双服务构建若仍因 Docker Hub 无法读取 `node:20-alpine` 元数据失败，只记录外部网络阻塞；不替换未知镜像源，不把 Core 单服务成功冒充为全栈镜像成功。

## 验收标准

- Python 契约、Node 回归、生产构建和差异检查通过。
- Core 空数据容器使用 Gunicorn 健康运行。
- 只有一个 Web worker，只有一套后台调度线程。
- 本地开发入口仍可用。
- Core 仍只在 Compose 内部网络可达。
- 测试期间没有真实业务写动作，没有真实凭据落盘。
- 当前 Vite、Express 和模拟 Core 预览进程保持可用。

## 回滚

Gunicorn 验收失败时恢复 Dockerfile 的 `python -m app.main`，移除 Gunicorn 配置与依赖；保留统一后台启动函数。回滚不删除或迁移任何 `data/`、`db/`、`upload/` 内容。

## 验收结果

- Python 契约测试 10/10、Node 回归 40/40、Python 编译和生产构建通过。
- Core 镜像使用 `gunicorn 23.0.0`，实际进程为一个 master 和一个 `gthread` worker；后台运行时启动日志一次，未出现 Flask development server 警告。
- 容器达到 `healthy`，24/24 并发只读状态请求成功，worker 未重启。
- `12388/tcp` 无宿主映射，三个命名卷保持独立；九个外部自动动作环境变量均为 `0`。
- 测试没有导入真实 NasEmby 台账，没有调用任何真实外部写接口，用户提供的地址、密码和 API Key 未写入仓库。
- 全量安全扫描命中的动态 SQL 标识符来自本地 SQLite `PRAGMA table_info`，用户标题和 TMDB ID 均使用参数绑定；本次新增运行时文件没有命令注入、SSRF、XSS 或硬编码凭据。
- NasEmby 原静态页面和关闭状态 legacy 模块的既有扫描告警保留为基线风险；Core 无宿主端口、Express 不代理原页面，不能把该页面作为公开入口。
- 完整中控镜像仍因 Docker Hub OAuth 连接被远端关闭而无法读取 `node:20-alpine` 元数据；Core 单服务镜像和本地 `npm run build` 已通过，不把它们冒充为完整双服务镜像成功。
- 2026-07-16 在订阅数据归属修正后复验：Core 单服务无凭据启动为 `healthy`，无宿主端口映射，`data/db/upload` 三个命名卷均挂载；执行一次容器重启后健康恢复、卷内缓存与活动日志仍存在，每次启动只有一套 Gunicorn worker 和一次后台运行时初始化。测试结束后容器与网络已清理，命名卷保留，未写订阅或触发 provider 后处理。
