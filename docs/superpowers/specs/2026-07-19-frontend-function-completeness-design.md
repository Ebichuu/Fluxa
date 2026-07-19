# 前端功能完整性优化设计

## 目标

在不修改旧 NasEmby 源码、不改变后端安全闸门和 HTTP 契约的前提下，补齐 React 前端对阶段 6/7 能力的操作入口，并改善 RSS 种子库与实时状态页面的请求可靠性。

## 范围

### 订阅操作

- 在现有订阅详情视图内增加质量观察状态与设置入口。
- 增加 Torra 人工分析、候选下载和 RSS 匹配分析操作。
- 增加 MoviePilot 预览与确认推送操作。
- 所有异步动作显示 `202 + Location` 返回的动作状态，并通过统一轮询读取脱敏结果；浏览器不自行拼接外部 ID、URL 或 Token。

### RSS 种子库

- 使用后端已有 `limit/offset/total` 实现上一页、下一页和当前页状态。
- 清空搜索时立即重新加载未过滤结果。
- 过滤、搜索和手动刷新使用请求序号或取消信号，旧响应不能覆盖新筛选结果。

### 请求与轮询

- 在 `src/services/api.ts` 增加统一 JSON 请求函数，集中处理超时、取消、错误消息和 204 响应。
- 为总览、控制室、任务中心的定时读取增加 in-flight 保护和卸载取消。
- 轮询失败保留当前可用数据，仅更新错误反馈，不把暂时网络错误误判为业务空状态。

## 组件边界

- `src/services/api.ts`：仅负责类型化 HTTP 调用和动作轮询，不包含页面状态。
- `src/hooks/usePolling.ts`：负责固定间隔、请求序号、AbortController 和卸载清理。
- `src/components/pages/DiscoverPage.tsx`：保留现有发现/订阅页面结构，在订阅详情抽屉插入操作区；不进行整页重写。
- `src/components/pages/RssSeedLibraryPage.tsx`：增加分页状态和查询请求保护。

## 错误与安全

- 保持后端返回的 `code/error/request_id` 脱敏语义，前端只展示 `error` 或固定兜底文案。
- 取消和超时显示可重试状态，不弹出重复错误。
- 需要确认的下载/推送动作继续使用现有确认对话框和幂等键。
- MoviePilot 闸门关闭、Torra/qB 不可用或观察窗口不满足时，展示后端阻塞原因并禁用提交。

## 验收标准

- 订阅详情可从 React 发起并查看阶段 6/7 的所有人工动作，不泄露外部凭据。
- RSS 种子超过 50 条时可以翻页；清空搜索后列表立即恢复。
- 页面切换、筛选快速切换、网络慢响应时无旧数据覆盖新状态、无卸载后 setState。
- `npm run typecheck`、`npm run build` 和后端 `python -m unittest discover -s tests -v` 全部通过。
- 不修改 `docs/references`、legacy NasEmby 源码或后端业务实现。

