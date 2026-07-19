# 控制室 MoviePilot 预留信息降级设计

## 背景

控制室当前在英雄区和核心服务卡片之间放置一块完整的 MoviePilot 预留面板。该区域只展示兼容状态，没有控制室内可执行的操作，却占用首屏较大空间，使未来备用能力压过 Torra、qBittorrent、Symedia 和 Emby 的真实运行状态。

MoviePilot 的人工预览与确认推送已经位于订阅详情中，系统设置也保留连接配置。因此控制室无需继续承担完整的 MoviePilot 面板。

## 决策

采用紧凑底部状态条方案：

1. 删除控制室顶部的 `ops-control-integrations` 大面板。
2. 英雄区之后直接显示四项核心服务及服务检查器。
3. 在现有 `ops-control-foot` 中保留 Torra 主通道说明，并增加低强调的 MoviePilot 备用状态。
4. MoviePilot 状态只显示 `读取中`、`未配置`、`已配置` 或 `可用` 等只读信息。
5. 控制室不新增 MoviePilot 操作按钮；预览和推送继续由订阅详情负责。

## 数据与行为

继续使用现有兼容状态接口和 `integrations` 数据，不增加 API、不改变刷新周期，也不触发 MoviePilot、Torra 或 qBittorrent 写操作。

状态映射：

- 接口尚未返回：`MoviePilot 备用 · 读取中`
- 未配置：`MoviePilot 备用 · 未配置`
- 已配置但不可用：`MoviePilot 备用 · 需检查`
- 已配置且连接正常：`MoviePilot 备用 · 可用`

该状态只用于说明备用能力是否存在，不把 MoviePilot 计入四项核心服务在线数量。

## 布局

桌面端的底部状态条保持单行：主通道说明、Torra 状态标签、MoviePilot 备用标签和补充说明依次排列。MoviePilot 标签使用比 Torra 更低的视觉权重。

移动端允许状态条自动换行或变为单列，标签不截断，不产生横向滚动。删除顶部面板后不保留占位高度。

## 视觉与交互

- 沿用控制室现有深色和浅色主题令牌。
- MoviePilot 标签使用中性色边框和表面，不使用主操作蓝色。
- 状态变化不弹窗、不提示成功，也不产生额外动画。
- 标签不是按钮，不设置可点击外观。
- 保留现有减少动效、减少透明度和高对比度降级规则。

## 范围边界

本次只修改：

- `src/components/pages/ControlRoom.tsx`
- `src/styles/control-room.css`
- 必要时补充共享浅色主题选择器

不修改订阅详情、MoviePilot 后端运行时、API 契约、系统设置、影院大厅或其他服务卡片行为。

## 验收标准

1. 控制室首屏不再出现 MoviePilot 大面板。
2. 英雄区后直接显示核心服务卡片。
3. 底部状态条能准确显示 MoviePilot 只读状态。
4. MoviePilot 不计入核心服务在线数量。
5. 深色、浅色及移动断点均无溢出或多余占位。
6. 订阅详情中的 MoviePilot 人工备用操作保持不变。
7. `npm run build`、现有后端测试和 `git diff --check` 通过。
