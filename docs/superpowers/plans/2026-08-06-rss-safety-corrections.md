# Fluxa RSS 精准下载安全修正计划

日期：2026-08-06  
状态：实施中  
基线：远端提交 `27e0ae8`  
实施边界：在独立干净 worktree 中修改 Fluxa，不修改当前工作树，不修改真实数据库，不触发 qB、Torra、115 或 Symedia 写操作。

## 第一阶段：补齐测试复现

先增加失败测试，确认四个问题都能稳定复现：

1. qB 同目标任务处于 `queued` 或 `paused` 时，精准下载预览必须返回 `RSS_EXACT_QB_BUSY`。
2. 预览后保持 RSS GUID 不变，只修改 `download_url`，旧令牌执行必须返回 `RSS_EXACT_PREVIEW_STALE`，且不能调用 qB。
3. `Sense and Sensibility ... HDR10 x265 10bit` 必须解析为电影，季集均为空。
4. `pipelineOutcome` 为 Symedia 失败、顶层仍残留 qB 文案时，问题组必须显示 Symedia 原因。

## 第二阶段：修复安全阻断项

### 1. qB 目标占用状态

修改 `rss_subscription_match_runtime.py`：

- 将目标占用状态统一为 `downloading/stalled/queued/paused`。
- `completed` 不算活动下载，保持现有基线识别行为。
- 精准下载预览和旧的 qB preflight 共用同一判定，避免两套语义漂移。
- 不改变 qB 状态归一化和公开 API。

验收：

- 四种活动状态均阻止同目标提交。
- 不同季集不误拦截。
- 被阻止时 `qb.add_torrent()` 调用次数为零。

### 2. 预览绑定下载地址

修改精准下载指纹生成逻辑：

- 对当前完整 `downloadUrl` 计算单向摘要。
- 将摘要加入预览 fingerprint，不把原始地址加入响应、收据、活动日志或普通日志。
- 执行时重新读取 RSS 条目并计算摘要；地址变化则使旧预览失效。
- 新地址必须重新预览后才能提交。
- 不修改数据库结构和 API 请求格式。

验收：

- 同 GUID、地址变化时旧令牌失效。
- 地址未变化时原有执行和幂等回放正常。
- URL、Passkey、Cookie 不出现在响应、动作摘要和异常文本中。

## 第三阶段：修复解析与展示

### 3. 收紧 NxM 季集解析

修改 `private_rss_parser.py`：

- 保留 `1x03`、`1 x 03` 等真实季集格式。
- 明确排除 `x264/x265/x266` 编码标记。
- 继续优先识别 `S01E03`、`EP03`、中文“第 3 集”等明确格式。
- 不依靠媒体标题猜测身份。

新增正反例：

- `Show 1x03` -> `S01E03`
- `Show 1 x 03` -> `S01E03`
- `HDR10 x265 10bit` -> 无季集
- `x264/x266` -> 无季集
- 原有综艺 `S2026E70` 行为不变

部署后等待正常 RSS 收集器重新抓取，仓库现有 upsert 会更新同 GUID 条目的媒体范围，不执行手工数据库批量修改。

### 4. 统一问题组事实来源

修改 `problem_group_runtime.py`：

- 阶段、原因码和原因文案优先全部读取同一个 `pipelineOutcome`。
- 使用 `pipelineOutcome.reasonText` 生成问题组摘要。
- 仅在没有有效 `pipelineOutcome` 时兼容顶层旧字段。
- 成员详情继续保留原始投影，避免破坏 API 兼容性。

验收：

- Symedia 失败不会再显示成 qB 失败。
- 相同阶段和原因仍能稳定聚合。
- 首页和任务中心的问题组结论一致。

## 第四阶段：自动化验证

执行：

```powershell
cd services/nasemby-core
python -m unittest tests.test_private_rss_parser tests.test_problem_group_runtime tests.test_rss_subscription_match_runtime -v
python -m unittest discover -s tests -t . -v
cd ../..
npm run build
```

通过条件：

- 所有发现的后端测试通过。
- TypeScript 检查和生产构建通过。
- 无真实 qB、Torra、115 或 Symedia 写操作。
- API 脱敏测试继续通过。

## 第五阶段：发布与真实链路验收

1. 保持 `executionMode` 未授权、下载硬门禁关闭，先发布修复。
2. 只读观察至少几个完整 RSS 收集周期。
3. 确认生产 `x265` 样本不再显示为 `S10E265`。
4. 确认当前 Symedia 问题组显示真实 Symedia 原因。
5. 经用户明确批准后，临时开放人工模式和硬门禁。
6. 选择一个唯一冠军执行一次精准下载。
7. 完整核对：qB 单一任务 -> Torra 分类及秒传 -> 115 待整理 -> Symedia 归档 -> STRM -> Emby 可见。
8. 验收后立即恢复门禁关闭，保留动作收据和日志。

阶段 E 自动执行继续保持关闭。只有四项修复、全量回归和一次真实主链验收全部完成后，才重新评估自动模式。
