# Fluxa 网页配置管理设计

## 目标

管理员可以在 Fluxa 设置页修改全部应用级配置，不再需要直接编辑 `.env`。范围包括 Emby、qBittorrent、Torra、Symedia、TMDB、MoviePilot、115、Telegram、123 云盘、网络代理以及所有功能开关。

Docker 宿主机端口、卷挂载和镜像标签不属于应用内部配置，继续由 Compose 管理；网页不能修改这些容器外资源。

## 安全规则

- 设置接口只允许已登录管理员访问。
- 密码、Token、Cookie、API Key 和应用密钥不回传明文。
- 已保存的敏感项返回 `hasValue=true`，输入框保持空白；空白提交保留原值。
- 管理员可以显式选择“清除已保存值”，不能用空字符串误删凭据。
- API 只接受配置目录中声明的字段，拒绝未知键、换行和值类型不匹配。
- 日志、错误响应和活动记录不包含敏感值。

## 配置目录

后端维护唯一配置目录，字段包含：分组、键名、标签、控件类型、默认值、是否敏感、是否需要重启、校验规则和说明。前端根据目录渲染表单，不再维护另一份键名列表。

配置分组：

1. Emby
2. qBittorrent
3. Torra
4. Symedia
5. TMDB 与发现
6. MoviePilot
7. 115 与 123 云盘
8. Telegram 与 HDHive
9. 自动化与安全开关
10. 网络与高级兼容配置

目录覆盖 `.env.example` 中除 `MCC_DATA_ROOT` 外的应用字段，并覆盖现有 `CONFIG_FIELDS` 兼容字段。`MCC_DATA_ROOT` 只决定宿主机卷挂载，不能在容器内部修改。

## 持久化与生效

`PUT /api/v2/settings/runtime` 将配置原子写入 `/app/data/user.env`，该文件位于现有持久卷中。启动时 `load_runtime_env()` 继续以该文件覆盖 Compose 注入值，因此容器重建后配置仍保留。

保存时同步更新当前进程环境：

- 依赖环境映射动态判断的功能开关立即生效。
- Emby、qBittorrent、Torra 和 Symedia 客户端调用 `reconfigure()` 更新连接配置并清理认证缓存。
- TMDB、MoviePilot、115、Telegram 等按请求读取环境的能力在后续请求中使用新值。
- 只有调度线程是否创建等启动级配置标记为 `restartRequired=true`，页面明确显示“重启后生效”。

网页不在保存后主动杀死 Gunicorn 或重启容器。

## HTTP 契约

### 读取

`GET /api/v2/settings/runtime`

返回字段目录和脱敏状态：

```json
{
  "success": true,
  "groups": [
    {
      "id": "emby",
      "title": "Emby",
      "fields": [
        {
          "key": "EMBY_PASSWORD",
          "label": "密码",
          "type": "secret",
          "value": "",
          "hasValue": true,
          "restartRequired": false
        }
      ]
    }
  ]
}
```

### 保存

`PUT /api/v2/settings/runtime`

```json
{
  "values": {
    "EMBY_BASE_URL": "http://emby:8096",
    "MCC_PRIVATE_RSS_ENABLED": true
  },
  "clearSecrets": ["EMBY_PASSWORD"]
}
```

返回保存后的脱敏目录、立即生效字段和需要重启的字段。请求失败时不写入部分配置。

## 页面交互

- 设置页保留现有获取路线与访问保护。
- “服务连接”改为可编辑配置区，按软件分组，支持分组折叠和关键字筛选。
- 布尔值使用开关；选择项使用下拉菜单；数字使用数字输入；秘密使用密码输入；长文本使用多行输入。
- 每组单独保存，避免修改一个软件时提交全部秘密字段。
- 保存状态显示“已保存并立即生效”或“已保存，重启后生效”。
- 未修改时保存按钮禁用；失败时保留输入内容并显示服务端错误。

## 测试与验收

- 配置目录覆盖所有应用字段且不存在重复键。
- 敏感值读取永不回显；空白保存保留；显式清除有效。
- 未知键、非法 URL、非法布尔值、换行和超长值被拒绝。
- 写入使用原子替换，持久化文件不包含未声明字段。
- 保存后环境映射更新，四个核心客户端重新配置。
- 设置页可加载、筛选、修改、保存和显示重启提示。
- Python 全量测试、TypeScript、生产构建、Compose 解析、文档链接和安全扫描通过。

