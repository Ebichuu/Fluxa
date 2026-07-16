# 日常巡检与故障排查

以下检查默认只读。命令中的 SSH 连接使用本机已有认证，不写密码。

## 快速健康检查

### 服务探活

```sh
for p in 8080 8017 8095 8096 9026 9029 19798; do
  nc -z -w 2 192.168.50.50 "$p" && echo "$p OPEN" || echo "$p CLOSED"
done
```

核心端口：qB `8080`、Refind `8017`、Symedia `8095`、Emby `8096`、FastEmby `9026`、Torra `9029`、CloudDrive2 `19798`。

### 容器状态

```sh
ssh Ebichu@192.168.50.50 \
  'docker ps --format "{{.Names}}|{{.Status}}|{{.Ports}}"'
```

检查重点：反复重启、健康检查失败、运行时间突然变短。

### 磁盘

```sh
ssh Ebichu@192.168.50.50 'df -h /vol1 /vol02'
```

超过 90% 立即处理；先找积压目录和权重删除目录，不直接删媒体。

## 秒传队列检查

### 目录数量和体积

```sh
ssh Ebichu@192.168.50.50 '
for d in \
  /vol02/1000-4-32d3f6a0/秒传 \
  /vol02/1000-4-32d3f6a0/秒传失败; do
  echo "$d"
  find "$d" -type f | wc -l
  du -sh "$d"
done'
```

`.secupload115_state.json` 是状态文件，不算媒体失败数。统计失败媒体时排除该文件。

### 任务状态

```sql
SELECT plugin_key, task_key, status, message, started_at, finished_at
FROM plugin_task_runs
WHERE plugin_key = 'secupload_115'
ORDER BY created_at DESC
LIMIT 30;
```

判断标准：

- 任务 `success` 只代表任务执行结束；仍要看 `message/result_json` 中的成功和失败数。
- `running/queued/pending` 长期不结束才是任务卡死。
- 主目录文件长时间没有任务记录、没有 SHA1 缓存，属于漏消费。
- 失败目录持续增长且同一文件重试次数上涨，优先停重试并查 115 错误，避免风控。

### 分类路径一致性

逐个确认：

```text
source_path = /qbdownload/秒传/<分类>
temp_path   = /qbdownload/秒传失败/<相同分类>
dest_path   = /00-待整理/<相同分类>
```

任何跨分类路径都按配置错误处理。

### 分类根目录防删

`empty_folder_cleaner` 禁止直接扫描 `/qbdownload/秒传`。该插件会递归删除扫描根下面不含有效视频的子目录；如果扫描根设在秒传总目录，空的分类根也会被删除，导致对应 `secupload_115` 监控失效。

固定做法：

- 每个分类单独一条清理规则，扫描根为 `/qbdownload/秒传/<分类>`；
- 只让插件清理分类根下面的空资源目录；
- 主秒传、秒传失败和 qB 下载目录都必须保留 8 个分类根；
- 修改后重启 Torra，日志中应出现 8 条“115秒传 开始监控目录”，且没有“监控源目录不存在”；
- 再确认主队列文件被启动扫描接走，失败文件进入同分类失败目录。

当前 8 条清理计划每 12 小时运行，按分类错峰在第 15 至 22 分钟执行。旧的 `/qbdownload/秒传` 根规则保留但必须保持停用。

### 豆瓣榜单订阅落到分类目录

检查 `subscriptions.save_path` 时，豆瓣榜单创建的订阅只能落在以下 8 个目录：

```text
/vol02/1000-4-32d3f6a0/torra/00-日漫
/vol02/1000-4-32d3f6a0/torra/01-国漫
/vol02/1000-4-32d3f6a0/torra/02-国产剧
/vol02/1000-4-32d3f6a0/torra/03-日韩剧
/vol02/1000-4-32d3f6a0/torra/04-欧美剧
/vol02/1000-4-32d3f6a0/torra/05-港台剧
/vol02/1000-4-32d3f6a0/torra/06-综艺
/vol02/1000-4-32d3f6a0/torra/10-电影
```

排查顺序：

1. 联查 `douban_rank_subscriber_records.subscription_id` 与 `subscriptions.id`，确认 `resolved_category` 和 `save_path` 映射一致。
2. 确认四个 `douban_*subscription_path*` 触发器仍存在。
3. 从 qB API 检查 `save_path`，总目录、`07-纪录片`、`09-欧美动画`、`99-未分类` 均不属于当前完整接力。
4. 修复现有 qB 任务时使用 qB `setLocation`，禁止直接移动仍在做种的文件。
5. 等待 `plugin.file_event_mover_observer_candidate` 和 `plugin.secupload_observer_candidate` 完成，再重载 Torra 并确认根目录没有新任务。

豆瓣分类合并规则：南亚剧并入 `03-日韩剧`，欧美动画并入 `04-欧美剧`，电影统一进入 `10-电影`。没有有效豆瓣历史关联的旧订阅（例如 `/qbdownload/AUD`）不自动迁移。

## 入库检查

### 区分四个阶段

1. qB 下载完成。
2. Torra 秒传成功。
3. Symedia `TransferHistory.status = 1`。
4. STRM 生成并被 Emby 索引。

前一阶段成功不等于后一阶段成功，汇报时不得混用。

### Symedia 归档历史

```sql
SELECT date, status, category, title, src, dest, errmsg, file_size
FROM transferhistory
ORDER BY id DESC
LIMIT 100;
```

实际表名以 `sqlite_master` 为准。统计时分别给出成功数、失败数、失败原因和最终目标路径。

### STRM 结果

对比：

- Symedia `SyncHistory` 的 `strm_count`；
- STRM 目录文件数；
- Emby 最近新增项目；
- Symedia/Emby 刷新日志。

## 常见故障

### 115 `index out of bounds on dimension 1`

- 表现：秒传或回退上传失败，通常带目标 `remote_dir/cid`。
- 先查：目标目录是否存在、CID 是否有效、客户端库版本、失败文件是否集中在同一分类。
- 禁止：无限重试同一批文件。

### Torra 升级后容器重启

- 查日志是否为 `urllib3.fields.format_header_param` 缺失。
- 已知兼容约束：`urllib3.future < 2.22`。
- 升级 `p115client` 时避免无控制地连带升级依赖。

### 秒传成功但删种脚本不删

- 查 `secupload_sha1_cache` 是否存在对应文件。
- 查失败状态文件和 `TransferHistory`，不要只看脚本的“删除成功”文字。
- shell 中 POST 数据的 `&` 必须被完整引用。

### Symedia 入库失败

- 先查 `TransferHistory.errmsg`。
- 再查源路径、目标路径、TMDB/季集识别和权重详情。
- 最后查 Archive/Transfer/AutoSymlink 日志，禁止先手动补 STRM。

## 修改验证模板

每次变更至少记录：

```text
时间：
对象：
变更前：
变更内容：
变更后：
服务是否重载：
任务是否实际运行：
目录/数据库/API 验证：
回滚方法：
```

## Hermes 每日自动巡检

- 任务：`daily-reflection`（ID `1059a5a8bd79`）。
- 时间：每日 `23:40`，Asia/Shanghai。
- 结果：写入 `/Users/zou/.hermes/hermes-agent/memory/YYYY-MM-DD.md` 的“影音中心巡检（23:40）”章节，并把简短摘要发送到原会话。
- 范围：核心服务与容器、磁盘、Torra 订阅和任务、秒传及归档队列、Symedia 当日入库和 STRM、qB 异常任务、Emby/CloudDrive2 可用性、关键外部依赖错误。
- 原则：巡检只读；不自动重启、删除、移动、重试或修改配置；不得在输出中记录任何认证信息。
- 口径：`stalledDL` 且无种属于资源源头问题；“源分 <= 目标分，取消覆盖”属于正常洗版拒绝；取不到的数据明确标为“未取得”，不得猜测为正常。
- 变更前 cron 快照：`/Users/zou/.hermes/cron/backups/jobs-before-media-inspection-20260713-052132.json`。
