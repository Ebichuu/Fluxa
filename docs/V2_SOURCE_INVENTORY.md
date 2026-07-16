# v2 源码与资料完整性清单

基线：原项目提交 `7368790`  
核对日期：2026-07-17

## 结果

原提交共有 152 个受 Git 管理的文件：

| 去向 | 数量 | 说明 |
| --- | ---: | --- |
| v2 原路径 | 113 | 当前生产源码、测试、部署和主要文档 |
| `docs/references/nasemby-original-ui/` | 7 | 原静态页面 HTML、JS、CSS 和四张图片 |
| `docs/references/original-project-omitted-7368790/` | 32 | 首轮未迁入的设计、计划、样例和补丁资料 |
| 未找到 | 0 | 原提交文件均已在 v2 当前路径或参考快照中保留 |

## 生产源码结论

- 原提交中的 Python 业务模块没有缺失。
- `services/nasemby-core/app/main.py` 已恢复原核心接口和调用关系，并叠加默认隔离开关。
- React、Mineradio、测试、根 Dockerfile 和 Compose 继续位于当前运行路径。
- 原静态管理页面只保留为参考，不注册生产路由，也不进入 Docker 镜像。
- 旧设计与计划只保留为参考；当前决策以根 README 和 `docs/` 活动文档为准。

## 参考快照内容

### 原静态页面：7 个文件

- `app.js`
- `styles.css`
- `index.html`
- 四张 NasEmby Logo 图片

清单和 SHA-256 见 `docs/references/nasemby-original-ui/README.md`。

### 省略资料：32 个文件

- 7 份旧首页、Mineradio 和参考 UI 资料。
- 9 份原 `docs/superpowers/plans/` 实施计划。
- 10 份原 `docs/superpowers/specs/` 设计与审计。
- 3 份根目录 Mineradio 迁移资料。
- `services/nasemby-core/.env.example`、模块 Dockerfile 和 `patches/README.md`。

这些文件保持原提交目录结构，存放在 `docs/references/original-project-omitted-7368790/`。

## 后续规则

以后每次迁移或整理都要重新生成三类结果：原路径存在、参考快照存在、未找到。只有“未找到”为 0，且接口能力矩阵没有失配，才允许把 v2 标记为源码完整。
