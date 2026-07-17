# Python 统一后端设计

## 1. 定位

本模块是媒体控制中心唯一生产后端。它保留 NasEmby 可运行源码作为订阅与发现业务基础，同时吸收原中控边界能力：认证、React 静态托管、Mineradio 桥接、外部服务适配和统一任务链。

目录名称继续使用 `services/nasemby-core`，只是为了保持 Python 导入和补丁来源稳定；部署中不存在第二个 Core 服务。

## 2. 架构

```text
浏览器
  → 单端口 8787
  → Gunicorn（1 worker / gthread / 4 threads）
  → Flask create_app()
      ├─ 认证、Origin、请求 ID、错误脱敏
      ├─ React dist 与 SPA 回退
      ├─ Mineradio 原始资源与 postMessage 桥接
      ├─ 公开 API 兼容层
      │   ├─ 发现与资源搜索
      │   └─ 订阅、详情、日历与配置
      ├─ NasEmby 原业务函数与唯一台账
      ├─ Emby / qB / Torra / Symedia / 任务链
      └─ Torra 固定推送 / 系统指标 / 延期保留的网盘模块
```

Node.js 只存在于 Docker `web-build` 阶段。生产镜像不复制 Node、npm、服务端 JavaScript 或 `dist-server`。

## 3. 应用组装

`app/main.py:create_app()` 按以下顺序注册：

1. HTTP 请求 ID和统一错误处理。
2. 整站访问保护。
3. Mineradio、Emby、qB、Torra、Symedia、刷新与任务链运行时。
4. 发现和订阅公开兼容层。
5. v2 Torra 推送、系统指标、集成与延期保留的网盘运行时。
6. React 静态目录与 SPA 回退。
7. NasEmby 原 Blueprint，供 `/api/status`、`/api/health` 和源码兼容保留。
8. 保留核心接口隔离守卫。

公开兼容路由先注册，保证 React 使用的路径命中白名单映射。原 Blueprint 中未列入公开契约的 115、Telegram、HDHive、缓存预热和 provider 核心入口继续保留，默认由守卫返回 `503 PRESERVED_CORE_API_DISABLED`。

## 4. API 兼容层

冻结契约共 47 条，机器清单位于 `docs/contracts/http-api-contract-v1.json`。

### 公开浏览器响应

`app/contract_mapping.py` 只映射 React 明确消费的字段：

- 订阅：camelCase 身份、季号、进度、分类和来源。
- 详情：TMDB 元数据、演员、季集和明确要求展示的入库路径。
- 日历：日期、季集、进度与入库证据。
- 发现：来源、TMDB 身份、海报、评分和分页。
- 资源搜索：候选、来源、季集覆盖和脱敏错误。

未知字段、原始上游包络、Token、Cookie 和异常正文不会透传浏览器。

### 内部诊断响应

`/api/internal/nasemby-core/*` 保留原 NasEmby 数据形状，便于核对源码行为。它们仍受会话认证保护，不是外部服务间的第二个网络层。

## 5. 订阅所有权

唯一台账：

- `db/discover_subscription_items.json`
- `db/discover_subscriptions.json`

用户从媒体控制中心保存订阅时，Python 把 React 平铺字段转换为 NasEmby 原 `item`，再调用 `save_subscription_item()`。列表、详情、日历、任务链和调度都读取同一份文件。

分类和改季是台账字段更新：

- 分类写 `media_category`。
- 改季同步 `target_season`、`current_season`、`latest_season`、`season_number` 和 `season_name`。

这两类编辑不调用保存订阅后的 provider 队列，避免普通字段修改意外触发外部获取。

不导入外部 NasEmby 台账；`POST /api/subscriptions/import-nasemby` 只保留路径并明确返回禁用。

## 6. 获取策略

- 默认主通道：PT / Torra。
- 自动云盘兜底：关闭。
- `cloud_then_pt`：不支持。
- Torra 推送前必须同时满足：稳定 TMDB 身份、八分类、非空下载器 ID、非空分类保存路径、在线查重完成和推送开关开启。
- 推送载荷中的 `downloader_id` 与 `save_path` 不允许为空。
- 网盘第二通道独立于旧 `resource_then_pt`：全局和订阅级开关默认关闭，PT 已有关联证据时禁止网盘转存。
- 候选预览只返回 15 分钟有效的随机候选 ID 与脱敏摘要；完整链接和密码只留在单 worker 内存。
- 单条转存要求明确确认、12-128 字符幂等键、60 秒订阅冷却，并在执行前重新读取 Torra、qB、Symedia 和 Emby 证据。

上述网盘运行时当前只作为延期源码和契约基线保留，React 不调用。当前订阅动作只允许固定推送 Torra；Symedia 只读取 115 后整理与入库证据，MoviePilot 自动补齐留待未来设计。

Torra v2 推送要求确认、12–128 字符幂等键、60 秒订阅冷却，并从唯一台账重建条目后复用现有分类、保存路径、下载器 ID 和在线查重逻辑。

`push-preview` 是只读证据接口；`push` 在服务端重新构建计划和查重，不能复用浏览器提交的旧预览。

## 7. 调度模型

Gunicorn 固定单 worker，避免多进程重复调度。后台组件：

- HDHive 到期检查线程。
- 发现缓存预热线程。
- 订阅调度线程，仅在 `MCC_SUBSCRIPTION_SCHEDULER_ENABLED=true` 时启动。

当前 Compose 固定关闭订阅调度。未来开启多 worker 或多副本前，必须先引入调度选主和台账并发写方案。

## 8. 写入闸门

当前部署固定：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
NASEMBY_CORE_WRITE_ENABLED=false
MCC_PRESERVED_CORE_API_ENABLED=false
TORRA_PUSH_ENABLED=false
MCC_INTEGRATION_PROBE_ENABLED=false
MCC_INTEGRATION_MANAGEMENT_ENABLED=false
MCC_TELEGRAM_MANAGEMENT_ENABLED=false
MCC_HDHIVE_MANAGEMENT_ENABLED=false
MCC_CLOUD_SEARCH_ENABLED=false
MCC_CLOUD_TRANSFER_ENABLED=false
```

`NASEMBY_CORE_WRITE_ENABLED` 控制订阅保存、分类、改季、配置、执行、屏蔽、删除和清空。Torra 推送还必须通过 `TORRA_PUSH_ENABLED`。

`MCC_PRESERVED_CORE_API_ENABLED` 仅用于原核心接口的模拟兼容测试。生产不得整体开启；每组能力接入 React 前必须建立独立的字段白名单、写入开关、幂等和审计。

qB 暂停/恢复与 Emby 刷新是已经存在的人工可回滚动作，分别使用目标复查、执行结果复查、证据锁和冷却保护。部署只读验收阶段不调用这些接口。

## 9. 认证与安全

### 威胁模型

主要防护：

- 未授权用户读取媒体数据、服务状态和管理 API。
- 跨站请求伪造危险动作。
- 上游异常、URL 或凭据泄漏到浏览器和日志。
- 外部图片代理引发 SSRF。
- 路径遍历读取 Mineradio 或 React 目录外文件。
- 旧 NasEmby 高风险管理路由随统一端口意外暴露。
- 重复调度、重复推送和空分类路径造成外部副作用。

### 安全策略

- 生产必须配置至少 16 字符的 `MCC_ACCESS_KEY`。
- 使用固定七天、HttpOnly、SameSite=Strict 的 HMAC 会话 Cookie。
- 危险方法只接受同源或 `MCC_ALLOWED_ORIGINS` 中的精确 Origin。
- 登录五次失败锁定 15 分钟；登录页使用严格 CSP。
- `/healthz` 是唯一无认证业务健康探针，只返回 `status=ok`。
- 图片代理拒绝私网 IP、私网 DNS、凭据 URL、重定向和无效图片魔数。
- 静态资源使用安全路径拼接；`api/auth/mineradio` 前缀不进入 SPA 回退。
- API 异常返回固定错误与请求 ID，不返回堆栈、内部 URL 或异常正文。
- 保留核心接口在统一入口默认返回 503；Flask 原 `/static/*` 注册关闭，原 NasEmby 静态页面脚本不对外提供，但源码作为迁移参考保留。

### 信任边界

- 浏览器不接收服务端已保存的 Emby、qB、Torra、Symedia、TMDB、115、Telegram 或 HDHive 凭据；登录表单中的临时输入不写入浏览器存储。
- 环境变量和 `data/user.env` 属于服务端受保护配置。
- 外部服务全部是不可信上游，响应先解析、校验和映射。
- fnOS 反向代理只信任一层；公网必须使用 HTTPS 和防火墙限制源站端口。

### 已知限制

- 订阅 JSON 写入仍是单实例模型，不支持多进程并发。
- 原 NasEmby legacy 源码和核心路由仍在仓库中作为业务来源与契约基线；没有等价替代和回归测试前不得删除。
- 部分受保护 HDHive 资产只能在匹配的 Python 3.13 / Linux 环境运行。
- 当前阶段没有完成真实订阅到入库闭环，外部路径和下载器 ID仍需 fnOS 实机核对。
- 自动云盘兜底当前只计算和展示状态，没有后台自动执行器；人工搜索/转存同样默认由环境闸门关闭。

## 10. 持久化

- `data/`：配置、活动日志、刷新冷却状态。
- `db/`：订阅、订阅配置、详情/发现缓存。
- `upload/`：运行文件和会话资产。

Compose 通过 `MCC_DATA_ROOT` 把三个目录映射到同一个 fnOS 根目录。升级和回滚整体备份，不能手工拼接台账。

## 11. 测试与完成标准

自动验证包含：

- 47 条冻结 v1 路由和 16 条 v2 路由均在 Python 中存在。
- 42 条受保护路由逐条返回 401。
- 所有受保护写接口逐条拒绝错误 Origin。
- React API 引用全部属于 client 契约。
- 临时台账验证保存、列表、分类和改季，不连接真实 provider。
- Torra 推送只使用模拟客户端。
- 保留的高风险入口默认返回 503，模拟测试可显式开启。
- Docker 最终镜像不含 Node，重启后持久目录保留。
- Mineradio 片段使用冻结 SHA-256，桥接消息和原资源继续回归。
- v2 写接口逐条验证认证与 Origin；网盘候选脱敏、默认关闭、重复阻止和幂等回放使用模拟测试覆盖。

## 12. 回滚

代码优先回滚到上一个已验证镜像或归档标签；订阅数据不随代码回滚。恢复旧双服务归档时必须确保新容器已停止，不能同时启动两套后端或调度器。

## 13. 变更历史

### 2026-07-17 — PT 单一主线与当前页面收口

**变更内容**：当前订阅只提供 Torra 预览和推送；Symedia 不接收订阅推送。Telegram、HDHive / pansou、影巢和 115 分享转存从 React 隐藏但源码、路由与测试保留。新增缓存系统指标、活动日志页面、订阅导航和媒体抽屉优化；测试活动日志改用临时路径，移动端抽屉关闭时主动释放焦点。

**变更理由**：Torra 已负责 PT、qB 编排和 115 秒传，Symedia 已负责后续整理入库；中控不重复实现现有线路。先验证单条 PT 主链，再考虑 MoviePilot 或 Telegram 网盘扩展。

**影响范围**：订阅兼容层、系统指标运行时、React 导航/订阅/任务/设置/影院抽屉、HTTP v2 契约、测试和文档。NasEmby 网盘底层源码没有删除，Mineradio 核心视觉与桥接协议未修改。

### 2026-07-17 — PT 默认策略与 NasEmby 网盘安全接入

**变更内容**：默认订阅模式改为 Torra；旧资源优先默认配置迁移到 PT 主通道。新增 13 条 v2 接口、集成设置、网盘策略、候选预览、单条转存保护和任务中心支线状态。

**变更理由**：落实“PT 优先、网盘由开关控制”的产品决策，同时继续复用 NasEmby 115、Telegram、HDHive / pansou 业务源码，不整体开放旧高风险管理接口。

**影响范围**：订阅配置、任务链、系统设置、发现订阅卡、Python 应用装配、Docker 闸门、契约、测试和文档。影院大厅视觉、顶部导航外观和媒体队列未修改。

### 2026-07-17 — 恢复原核心接口并改为保留优先

**变更内容**：恢复 `main.py` 中 115、Telegram、HDHive、provider、配置和活动日志路由；增加默认 503 隔离开关、模拟测试、逐接口能力矩阵和原静态页面参考快照。

**变更理由**：底层模块仍在并不等于接口链路完整。保留准确路由、参数处理和调用关系，避免以后重新猜测业务语义或误删网盘能力。

**影响范围**：Python 应用入口、接口安全边界、测试、环境样例和 v2 文档。React 当前 47 条契约、页面结构、影院大厅、顶部导航和媒体队列未修改。

### 2026-07-17 — Python 后端统一完成

**变更内容**：补齐发现、订阅和内部诊断兼容层，增加写闸门与 legacy 管理入口守卫；切换单容器 Python 运行时；删除 Express 源码、运行依赖和旧 Node 后端测试。

**变更理由**：保持 NasEmby 原业务的同时消除双后端、双台账和双调度风险，并让 fnOS 部署只维护一个容器。

**影响范围**：后端运行时、Docker、API 契约测试、部署与回滚文档。React 页面结构、影院大厅、顶部导航、媒体队列和 Mineradio 原视觉未修改。
