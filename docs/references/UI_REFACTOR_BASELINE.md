# 工作页重构冻结基线

日期：2026-07-17

用途：状态首屏与 CSS 模块化实施期间，证明影院大厅没有代码、样式、交互或视觉变化。

## Git 基线

- 开始提交：`5a060ed6ba8dfaf384893c490dc4f98be740a965`
- `src/components/media-hall` tree：`0d088dbf019f66ac4e868118ddf93ccca2f4fefe`
- `vendor/mineradio-public` tree：`4e7cd40b93fa7d613777510ac2905499758db268`

实施期间以下路径必须保持 tree / blob 不变：

- `src/components/media-hall/**`
- `vendor/mineradio-public/**`

## 大厅 CSS 基线

浏览器递归读取全部样式表，序列化选择器包含 `.media-hall`、`.mineradio-`、`.media-queue-` 的 `CSSStyleRule`，并保留所属媒体查询条件。

- 规则数量：43
- SHA-256：`227bab132075bd6c33f2f660d93de381b1eedbf19f6382eff281435b39d79927`

拆分 CSS 后必须用相同算法重新计算；数量和摘要必须完全一致。

## 视觉基线

本地地址：`http://127.0.0.1:5173/`

| 视口 | 截图 SHA-256 |
| --- | --- |
| 1440×900 | `33defd10015bc253644476da97c81132aa0c6016ef61b14faf280884332c696e` |
| 1024×768 | `4f085211d9b12e10c350c762e47a8f6abb8c7577a9fc6af28cf22a29a357fd40` |
| 390×844 | `86095c09cd1b14af38644897e5819e9b12ee769b17889f920e0d9c182937478d` |

截图在浏览器验收会话中捕获；仓库只保存摘要，避免把一次性预览位图加入运行资料。

## 页面状态

- 当前导航：影院大厅。
- 数据来源：示例媒体库。
- 当前媒体库：电影库。
- 当前媒体：`Dune: Part Two`。
- 媒体抽屉类名：`media-queue-panel`，未展开、未固定。
- 页面告警元素：0。
- 浏览器控制台错误：0。

## 构建基线

- `src/styles/global.css`：5431 行。
- 生产 CSS：82005 bytes，gzip 14.55 kB。
- Vite 转换模块：1592。
- 基线构建：通过。

截图像素摘要可能因 GPU / WebGL 动画帧变化而不同，因此最终判断顺序为：冻结 tree → 大厅 CSS 摘要 → 计算样式与交互 → 人工截图对比。截图摘要只作辅助证据。
