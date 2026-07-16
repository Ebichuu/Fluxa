# 家庭影音自动化维护规则

本目录是老大家庭影音系统的长期维护入口。处理 Torra、Symedia、CloudDrive2、Emby、Refind、qBittorrent 或 115 链路前，按以下顺序读取：

1. `README.md`
2. `CURRENT_STATE.md`
3. `ARCHITECTURE.md`
4. 与任务相关的 `TORRA.md`、`SYMEDIA.md` 或 `RUNBOOK.md`

## 工作原则

- 先查实时状态，再查数据库和日志，最后才依据源码解释。
- 不用“应该”代替证据；无法从源码确认的逻辑要明确标注。
- 配置修改必须执行“修改前快照 → 增量修改 → 重载/重启 → 实际验证”。
- Docker Compose、JSON 和数据库配置只做定向修改，不整体覆盖。
- 涉及路径时先在 fnOS 实机验证，不能从容器映射反推宿主路径。
- 媒体入库优先让现有自动化链路处理，不手动创建 STRM。
- 对外动作先问；读取、检查、整理本系统内部资料可直接进行。

## 安全边界

- 文档不得保存密码、Cookie、Token、License、私钥或完整鉴权请求。
- 命令示例使用已有 SSH 配置或环境变量，不在命令行硬编码密码。
- 删除操作必须先验证目标、数量和可恢复性；默认只读诊断。
- 不把包含个人账号标识、115 鉴权字段的原始任务结果复制到文档。

## 证据优先级

1. fnOS 当前容器、数据库、目录和 API 的实时结果。
2. `/Users/zou/projects/torra-ctf/source/decrypted/` 与 `/Users/zou/projects/symedia/source/decrypted/` 中可读源码。
3. 反编译 `.das`、`.seq`、`.cdc.py` 的函数签名和常量。
4. `/Users/zou/projects/*/docs`、`reports`、`references` 中的旧分析。
5. 历史记忆和日志。

## 源码限制

- 两套源码均来自 PyArmor/BCC 处理后的静态提取，很多函数体仍是 `BCC native`。
- 反编译器会产生错误控制流，不能把残缺伪代码当成可运行源码。
- Torra `source/decrypted/.../secupload_115/` 的部分导出文件存在路径错配，内容属于 `site_cookie_login`；正确秒传提取结果在 `torra-ctf/archive/Torra/plugins/secupload_115/`。后续分析必须使用正确目录，并继续与运行数据库、任务结果和目录状态交叉确认。
- Symedia 权重洗版的 BCC ELF 可通过项目内 `tools/list_bcc_functions.py` 与 `tools/fix_bcc_elf_header.py` 建立函数地址映射；原始样本不得覆盖，只分析修复副本。

## 文档维护

- 稳定架构更新到 `ARCHITECTURE.md`。
- Torra 或 Symedia 内部机制分别更新到对应文档。
- 当前问题、配置变更和验证结果写入 `CURRENT_STATE.md`。
- 新故障的排查与恢复步骤沉淀到 `RUNBOOK.md`。
