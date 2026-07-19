# 非影院页面 Apple 风格交互设计

## 范围

- 覆盖总览、发现、订阅、订阅设置、种子库、任务中心、日历、控制室和设置。
- 明确排除影院大厅、`src/components/media-hall/` 及其视觉参数系统。
- 不改变后端 API、数据模型、MoviePilot/Torra/RSS 行为，也不引入新的动画依赖。

## 设计决策

### 弹窗基础层

- 所有原生 `window.confirm` / `window.prompt` 收敛到 `ConfirmDialog`。
- 弹窗使用受控 `open` 状态，在父页面关闭状态后保留 220ms 完成退出动画。
- 弹窗通过 React Portal 挂载到 `body`，避免页面入场 `transform` 改变 fixed 定位坐标系。
- 桌面端居中，移动端使用底部 sheet；进入和退出使用相同空间路径，不使用回弹。
- 遮罩点击只在按下和抬起都位于遮罩且移动不超过 6px 时关闭，避免滑动误触。
- 打开时锁定页面滚动并建立焦点陷阱，关闭后恢复触发按钮焦点。

### 动画与反馈

- 页面内容使用 280ms、最多 72ms 轻量错峰的淡入上移。
- 展开区和状态反馈使用 220ms、4px 位移的 disclosure 动画。
- 按钮在 pointer-down 阶段缩放反馈，普通交互不使用 bounce。
- 非影院主导航使用一个共享的半透明选中胶囊，按目标按钮的实际位置和宽度移动；不依赖 View Transitions API，旧浏览器也能保持连续过渡。
- 动画仅使用 `transform` 与 `opacity`，避免触发布局重排。

### 可访问性

- `prefers-reduced-motion` 关闭页面、弹窗和输入框动画。
- `prefers-reduced-transparency` 移除模糊并改用高不透明材质。
- `prefers-contrast: more` 提高遮罩和弹窗边框对比度。
- 保留 Escape 关闭、Tab 焦点循环、`aria-labelledby` 和 `aria-describedby`。

### 页面层级

- 页面头部统一为“上下文眉题 + 大号页面名 + 功能小标题 + 说明”。
- 桌面顶部间距统一为 104px，移动端为 138px，给固定导航保留空间但避免大面积空白。
- 非影院移动端导航按钮不再压缩，改为稳定尺寸的横向滚动列表。

## 验证

- `npm run build`
- 桌面视口逐页检查标题、间距和控制台错误。
- 390 x 844 视口检查弹窗尺寸、横向溢出和导航按钮重叠。
- 验证原生 JavaScript 对话框为零，Escape 关闭后焦点和页面滚动恢复。
