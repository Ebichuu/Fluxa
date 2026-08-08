# Fluxa RSS 关联状态与 IMDb-only 元数据修复设计

日期：2026-08-08
状态：已实施，待发布验收
实施边界：只修改 Fluxa 资源中心的展示与只读数据解析；不修改外部系统或 RSS 身份台账

## 1. 问题与目标

资源列表目前使用当前已加载候选组推断卡片状态。当资源自身已经是 `followState=linked`、但对应候选不在当前 10 个候选组中时，卡片会错误显示“未关联”。

只读媒体元数据解析目前要求 RSS 行先具备 TMDB ID，导致已有精确订阅匹配、但 RSS 本身只有 IMDb ID 的资源无法使用订阅标题、年份和海报。

本补丁只解决以下两点：

1. 资源关联状态始终以资源自身的 `followState` 为事实来源。
2. IMDb-only 资源可在本次 API 响应中使用唯一、有效的订阅匹配补充媒体元数据。

## 2. 前端状态契约

卡片和详情面板共用同一个状态函数，并显式接收资源自身的 `followState`、候选匹配和动作状态。

状态优先级固定为：

1. 存在明确候选及动作时，显示 `Torra 已接收` 或 `分析中`。
2. 存在明确候选及评分结果时，显示评分、`评分暂未确认` 或 `等待评分`。
3. 没有加载到候选，但资源 `followState=linked` 时，只显示 `已关联`。
4. 资源 `followState=unlinked` 时显示 `未关联`。
5. 老接口没有 `followState` 时，才允许使用是否存在候选作为兼容回退。

`linked` 只证明资源与追更有关，不证明已经开始评分。前端不得仅凭 `linked` 推断“等待评分”或“分析中”。桌面卡片、移动卡片和详情面板必须调用同一状态函数。

## 3. IMDb-only 只读元数据解析

元数据解析分成两条只读路径：

- RSS 已有规范 TMDB 身份时，保持现有 `匹配订阅 → 精确订阅 → 发现候选 → 本地缓存` 优先级。
- RSS 只有 IMDb 身份时，仅检查活动 RSS 匹配最终指向的订阅。

IMDb-only 补充必须同时满足：

1. 所有活动匹配去重后只指向一个 `subscription_key`；同一订阅的多条集级匹配仍视为唯一。
2. 该订阅当前存在，并具有合法媒体类型、TMDB ID 和可用标题。
3. RSS 与订阅季号都存在时必须一致；任一明确季号冲突则拒绝补充。
4. 同一订阅来源形成的标题不能冲突。

订阅缺失、多个不同订阅、季号冲突或标题冲突时，响应省略 `mediaTitle`、`mediaYear` 和 `posterUrl`。不得继续降级到发现候选或本地 TMDB 缓存猜测身份。

补充结果只存在于本次资源 API 响应和本地中文搜索计算中。不得写回 `rss_items.tmdb_id`、`identity_status` 或 `identity_source`，不得创建身份审计事件，也不得调用外部 TMDB 服务。以后需要持久化身份时必须走独立的身份回填流程。

海报继续使用现有 URL 清洗规则：去除查询参数与用户信息，拒绝本地、私网和非法地址。响应与日志不得包含订阅原始 payload、Token、Cookie、Passkey、RSS 下载地址或内部服务地址。

## 4. 测试与验收

前端测试覆盖：

- `linked` 资源但候选组未加载时显示“已关联”；
- `unlinked` 资源显示“未关联”；
- 有具体候选时继续显示动作和评分状态；
- 卡片与详情面板使用同一状态来源，响应式布局不另建状态分支。

后端测试覆盖：

- IMDb-only 唯一订阅匹配返回中文名、年份和清洗后的海报；
- 同一订阅的多条匹配仍可解析；
- 多订阅冲突、订阅缺失、季号冲突均不返回元数据；
- 原始 RSS 身份字段保持不变；
- 元数据解析不触发外部 TMDB 请求；
- poster URL 清洗与敏感字段过滤继续有效。

最终验收指标：

1. `followState=linked` 列表中的错误“未关联”标签为 0。
2. IMDb-only 且唯一订阅匹配的资源可显示中文名和安全海报。
3. 冲突资源不猜测、不补图、不改变原始身份字段。

## 5. 修改范围与回滚

预计修改：

- `src/components/pages/RssSeedLibraryPage.tsx`
- 对应前端测试
- `services/nasemby-core/app/rss_media_metadata_runtime.py`
- `services/nasemby-core/tests/test_private_rss_repository.py`

不修改数据库结构、API 请求格式、Torra/qB/Symedia/p115client，也不触发搜索、下载、归档或外部写操作。

回滚时可整体撤销本补丁；所有新增行为均为响应组装和展示逻辑，不需要数据库迁移或数据恢复。

## 6. 实施验证

- 资源仓库相关测试：`python -m unittest tests.test_private_rss_repository -v`，28 项通过。
- 完整后端回归：`python -m unittest discover -s tests -t . -v`，785 项通过。
- 前端状态回归测试已接入 `npm test`，覆盖 `linked`、`unlinked`、旧接口和已加载候选回退。
- TypeScript 类型检查与生产构建通过。
- 生产构建仍保留既有的单包超过 500 KB 提示，本补丁未增加新的运行依赖。
- 验证过程没有调用 Torra、qBittorrent、Symedia、p115client 或外部 TMDB 写接口。
