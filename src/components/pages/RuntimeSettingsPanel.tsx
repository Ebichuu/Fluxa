import { useEffect, useMemo, useState } from 'react';
import {
  ChevronDown,
  Eye,
  EyeOff,
  KeyRound,
  RotateCcw,
  Save,
  Search,
  Settings2
} from 'lucide-react';
import {
  getEmbyOverview,
  getIntegrationSummary,
  getQbittorrentSummary,
  getRuntimeSettings,
  getSymediaSummary,
  getTorraSummary,
  saveRuntimeSettings
} from '../../services/api';
import type {
  RuntimeSettingField,
  RuntimeSettingGroup,
  RuntimeSettingsResponse
} from '../../types/runtimeSettings';

const initiallyOpen = new Set(['connection']);

// 常用视图四组；未列出的字段全部进入"高级设置"，同一字段绝不重复出现。
const COMMON_GROUPS: Array<{ id: string; title: string; note: string; keys: string[] }> = [
  {
    id: 'connection',
    title: '连接',
    note: 'Emby、qBittorrent、Torra、Symedia、TMDB 的核心地址和凭据',
    keys: [
      'EMBY_BASE_URL', 'EMBY_API_KEY', 'EMBY_USER_ID', 'EMBY_USERNAME', 'EMBY_PASSWORD',
      'QB_BASE_URL', 'QB_USERNAME', 'QB_PASSWORD',
      'TORRA_BASE_URL', 'TORRA_TOKEN', 'TORRA_USERNAME', 'TORRA_PASSWORD', 'TORRA_DOWNLOAD_ROOT', 'TORRA_DOWNLOADER_ID',
      'SYMEDIA_BASE_URL', 'SYMEDIA_TOKEN', 'SYMEDIA_USERNAME', 'SYMEDIA_PASSWORD',
      'TMDB_API_KEY', 'TMDB_API_TOKEN'
    ]
  },
  {
    id: 'automation',
    title: '自动化',
    note: '追更扫描、Torra 同步、RSS、质量观察、推送和云盘能力',
    keys: [
      'MCC_SUBSCRIPTION_SCHEDULER_ENABLED', 'MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED',
      'MCC_PRIVATE_RSS_ENABLED', 'MCC_TORRA_QUALITY_WATCH_ENABLED', 'MCC_TORRA_REWASH_DOWNLOAD_ENABLED',
      'TORRA_PUSH_ENABLED', 'MCC_MOVIEPILOT_BACKUP_ENABLED',
      'MCC_CLOUD_SEARCH_ENABLED', 'MCC_CLOUD_TRANSFER_ENABLED'
    ]
  },
  {
    id: 'notification',
    title: '通知',
    note: 'Telegram Bot、会话和订阅/转存通知',
    keys: [
      'ENV_TG_BOT_TOKEN', 'ENV_TG_ADMIN_USER_ID',
      'ENV_TG_TRANSFER_NOTIFY_ENABLED', 'ENV_TG_TRANSFER_NOTIFY_CHAT_IDS',
      'ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED'
    ]
  },
  {
    id: 'security',
    title: '安全',
    note: '写入开关、外部管理权限和访问保护',
    keys: [
      'NASEMBY_CORE_WRITE_ENABLED', 'MCC_PRESERVED_CORE_API_ENABLED',
      'MCC_INTEGRATION_PROBE_ENABLED', 'MCC_INTEGRATION_MANAGEMENT_ENABLED',
      'MCC_TELEGRAM_MANAGEMENT_ENABLED', 'MCC_HDHIVE_MANAGEMENT_ENABLED'
    ]
  }
];

// 连接验证按变更字段前缀确定受影响服务，不依赖展示分组 ID。
const VERIFY_PREFIXES: Array<{ prefixes: string[]; service: string }> = [
  { prefixes: ['EMBY_'], service: 'emby' },
  { prefixes: ['QB_'], service: 'qbittorrent' },
  { prefixes: ['TORRA_'], service: 'torra' },
  { prefixes: ['SYMEDIA_'], service: 'symedia' },
  { prefixes: ['ENV_115_', 'ENV_123_', 'ENV_UPLOAD_'], service: 'cloud' },
  { prefixes: ['ENV_TG_'], service: 'telegram' },
  { prefixes: ['ENV_HDHIVE_'], service: 'hdhive' },
  { prefixes: ['MOVIEPILOT_', 'ENV_MOVIEPILOT_'], service: 'moviepilot' }
];

function servicesForKeys(keys: string[]) {
  const services = new Set<string>();
  for (const key of keys) {
    for (const { prefixes, service } of VERIFY_PREFIXES) {
      if (prefixes.some((prefix) => key.startsWith(prefix))) services.add(service);
    }
  }
  return [...services];
}

function valuesFrom(payload: RuntimeSettingsResponse) {
  return Object.fromEntries(
    payload.groups.flatMap((group) => group.fields.map((field) => [field.key, field.value]))
  );
}

function fieldMatches(field: RuntimeSettingField, query: string) {
  const haystack = `${field.label} ${field.description} ${field.key}`.toLocaleLowerCase('zh-CN');
  return haystack.includes(query);
}

const disabledEffects: Record<string, string> = {
  MCC_SUBSCRIPTION_SCHEDULER_ENABLED: '关闭后停止后台定时扫描，仍可手动更新追更。',
  MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED: '关闭后停止刷新 Torra 镜像状态，不会删除两边已有订阅。',
  NASEMBY_CORE_WRITE_ENABLED: '关闭后进入只读模式，已有追更和历史记录保留。',
  MCC_PRIVATE_RSS_ENABLED: '关闭后停止 RSS 采集，已收集的种子记录保留。',
  MCC_TORRA_QUALITY_WATCH_ENABLED: '关闭后停止创建新的质量观察，已有下载和入库不受影响。',
  MCC_TORRA_REWASH_DOWNLOAD_ENABLED: '关闭后仍可分析候选，但不会允许确认下载。',
  TORRA_PUSH_ENABLED: '关闭后新追更不再推送到 Torra，Torra 现有订阅不受影响。',
  MCC_CLOUD_TRANSFER_ENABLED: '关闭后不再允许云盘转存，已完成文件不会删除。'
};

function fieldImpact(field: RuntimeSettingField) {
  const disabled = disabledEffects[field.key] ?? '关闭后不再执行此能力，已有数据不会自动删除。';
  return {
    enabled: `开启后：${field.description}`,
    disabled,
    applies: field.restartRequired ? '保存后需重启 Fluxa 生效。' : '保存后立即用于后续请求。'
  };
}

async function verifyRuntimeService(serviceId: string) {
  if (serviceId === 'emby') {
    const result = await getEmbyOverview();
    return result.configured && result.connected ? '连接验证成功' : `配置已保存，但 Emby ${result.configured ? '暂不可用' : '尚未配置完整'}`;
  }
  if (serviceId === 'qbittorrent') {
    const result = await getQbittorrentSummary();
    return result.configured && result.connected ? '连接验证成功' : `配置已保存，但 qBittorrent ${result.configured ? '暂不可用' : '尚未配置完整'}`;
  }
  if (serviceId === 'torra') {
    const result = await getTorraSummary();
    return result.configured && result.connected ? '连接验证成功' : `配置已保存，但 Torra ${result.configured ? '暂不可用' : '尚未配置完整'}`;
  }
  if (serviceId === 'symedia') {
    const result = await getSymediaSummary();
    return result.configured && result.connected ? '连接验证成功' : `配置已保存，但 Symedia ${result.configured ? '暂不可用' : '尚未配置完整'}`;
  }
  const integrationId = ({ cloud: 'cloud115', telegram: 'telegram', hdhive: 'hdhive', moviepilot: 'moviepilot' } as const)[serviceId as 'cloud' | 'telegram' | 'hdhive' | 'moviepilot'];
  if (!integrationId) return '';
  const summary = await getIntegrationSummary(true);
  const service = summary.services.find((item) => item.id === integrationId);
  if (!service) return '';
  if (!service.configured) return `配置已保存，但 ${service.name} 尚未配置完整`;
  if (service.connected === true) return '连接验证成功';
  if (service.connected === false) return `配置已保存，但 ${service.name} 暂不可用`;
  return `配置已保存；主动连接验证当前未开启`;
}

async function verifyChangedServices(changedKeys: string[]) {
  const services = servicesForKeys(changedKeys);
  if (!services.length) return '';
  const results = await Promise.all(services.map((service) => verifyRuntimeService(service).catch(() => '连接验证未完成')));
  return results.filter(Boolean).join('；');
}

export function RuntimeSettingsPanel() {
  const [payload, setPayload] = useState<RuntimeSettingsResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState<Set<string>>(new Set());
  const [clearSecrets, setClearSecrets] = useState<Set<string>>(new Set());
  const [visibleSecrets, setVisibleSecrets] = useState<Set<string>>(new Set());
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(initiallyOpen));
  const [savingGroup, setSavingGroup] = useState('');
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [showTechnicalNames, setShowTechnicalNames] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    setError('');
    getRuntimeSettings()
      .then((next) => {
        setPayload(next);
        setValues(valuesFrom(next));
        setDirty(new Set());
        setClearSecrets(new Set());
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '配置加载失败'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const normalisedQuery = query.trim().toLocaleLowerCase('zh-CN');
  // 把后端目录重排为四个常用组 + 唯一高级组；同一字段只出现一次。
  const displayGroups = useMemo(() => {
    if (!payload) return [];
    const fieldByKey = new Map(payload.groups.flatMap((group) => group.fields.map((field) => [field.key, field] as const)));
    const commonKeys = new Set<string>();
    const groups: RuntimeSettingGroup[] = [];
    for (const definition of COMMON_GROUPS) {
      const fields = definition.keys
        .map((key) => fieldByKey.get(key))
        .filter((field): field is RuntimeSettingField => Boolean(field));
      fields.forEach((field) => commonKeys.add(field.key));
      if (fields.length) groups.push({ id: definition.id, title: definition.title, note: definition.note, fields });
    }
    const advancedFields = payload.groups.flatMap((group) => group.fields).filter((field) => !commonKeys.has(field.key));
    if (advancedFields.length) {
      groups.push({ id: 'advanced', title: '高级设置', note: '兼容与少用配置，日常无需修改', fields: advancedFields });
    }
    return groups;
  }, [payload]);

  const visibleGroups = useMemo(() => {
    if (!normalisedQuery) return displayGroups;
    return displayGroups
      .map((group) => ({
        ...group,
        fields: group.fields.filter((field) => fieldMatches(field, normalisedQuery))
      }))
      .filter((group) => group.fields.length > 0);
  }, [displayGroups, normalisedQuery]);

  const changeValue = (key: string, value: string) => {
    setValues((current) => ({ ...current, [key]: value }));
    setDirty((current) => new Set(current).add(key));
    setClearSecrets((current) => {
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  };

  const toggleClearSecret = (key: string) => {
    setClearSecrets((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setValues((current) => ({ ...current, [key]: '' }));
    setDirty((current) => new Set(current).add(key));
  };

  const toggleSecretVisibility = (key: string) => {
    setVisibleSecrets((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleGroup = (groupId: string) => {
    setOpenGroups((current) => {
      const next = new Set(current);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  };

  const saveGroup = (group: RuntimeSettingGroup) => {
    const groupKeys = new Set(group.fields.map((field) => field.key));
    const changedFields = group.fields.filter((field) => dirty.has(field.key));
    const nextValues: Record<string, string | boolean> = {};
    for (const field of changedFields) {
      if (clearSecrets.has(field.key)) continue;
      const value = values[field.key] ?? '';
      if (field.secret && !value) continue;
      nextValues[field.key] = field.type === 'boolean' ? value === 'true' : value;
    }
    const nextClears = [...clearSecrets].filter((key) => groupKeys.has(key));
    setSavingGroup(group.id);
    setMessages((current) => ({ ...current, [group.id]: '' }));
    saveRuntimeSettings({ values: nextValues, clearSecrets: nextClears })
      .then(async (next) => {
        setPayload(next);
        setValues((current) => {
          const merged = valuesFrom(next);
          dirty.forEach((key) => {
            if (!groupKeys.has(key)) merged[key] = current[key] ?? '';
          });
          return merged;
        });
        setDirty((current) => {
          const result = new Set(current);
          groupKeys.forEach((key) => result.delete(key));
          return result;
        });
        setClearSecrets((current) => {
          const result = new Set(current);
          groupKeys.forEach((key) => result.delete(key));
          return result;
        });
        const restartRequired = next.restartRequired ?? [];
        if (restartRequired.length) {
          setMessages((current) => ({ ...current, [group.id]: `已保存，${restartRequired.length} 项重启后生效` }));
          return;
        }
        const changedKeys = [...groupKeys].filter((key) => dirty.has(key));
        const verification = await verifyChangedServices(changedKeys).catch(() => '配置已保存，但连接验证未完成');
        setMessages((current) => ({
          ...current,
          [group.id]: verification ? `已保存并应用 · ${verification}` : '已保存并应用'
        }));
      })
      .catch((reason: unknown) => {
        const message = reason instanceof Error ? reason.message : '保存失败';
        setMessages((current) => ({ ...current, [group.id]: message }));
      })
      .finally(() => setSavingGroup(''));
  };

  if (!payload) {
    return (
      <article className="ops-settings-card ops-settings-card--wide runtime-settings runtime-settings--loading">
        <Settings2 aria-hidden="true" size={18} />
        <span>{error || '应用配置加载中…'}</span>
        {error && <button className="tool-link" disabled={loading} type="button" onClick={load}><RotateCcw size={14} />{loading ? '重试中…' : '重试'}</button>}
      </article>
    );
  }

  return (
    <article className="ops-settings-card ops-settings-card--wide runtime-settings">
      <header className="ops-settings-card__head runtime-settings__head">
        <div><span><Settings2 size={16} /></span><div><small>管理员配置</small><h2>软件连接与功能开关</h2></div></div>
        <strong>常用设置</strong>
      </header>

      <div className="runtime-settings__toolbar">
        <label className="runtime-settings__search">
          <Search aria-hidden="true" size={15} />
          <input
            aria-label="搜索配置项"
            placeholder="搜索服务、连接或功能开关"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="runtime-settings__toolbar-meta">
          <span><KeyRound aria-hidden="true" size={14} />敏感值不回显</span>
          <label className="runtime-settings__technical-toggle">
            <input
              checked={showTechnicalNames}
              type="checkbox"
              onChange={(event) => setShowTechnicalNames(event.target.checked)}
            />
            显示技术字段名
          </label>
        </div>
      </div>

      <div className="runtime-settings__groups">
        {visibleGroups.map((group) => {
          const expanded = Boolean(normalisedQuery) || openGroups.has(group.id);
          const groupDirty = group.fields.some((field) => dirty.has(field.key));
          return (
            <section className={group.id === 'advanced' ? 'runtime-settings__group runtime-settings__group--advanced' : 'runtime-settings__group'} key={group.id}>
              <button
                aria-expanded={expanded}
                className="runtime-settings__group-toggle"
                disabled={Boolean(normalisedQuery) || Boolean(savingGroup)}
                type="button"
                onClick={() => toggleGroup(group.id)}
              >
                <span><strong>{group.title}</strong><small>{group.note}</small></span>
                <span>{group.fields.length} 项<ChevronDown aria-hidden="true" className={expanded ? 'is-open' : ''} size={17} /></span>
              </button>
              {expanded && (
                <div className="runtime-settings__group-body">
                  <div className="runtime-settings__fields">
                    {group.fields.map((field) => {
                      const markedForClear = clearSecrets.has(field.key);
                      const impact = field.type === 'boolean' ? fieldImpact(field) : null;
                      return (
                        <label className={`runtime-setting runtime-setting--${field.type}`} key={field.key}>
                          <span className="runtime-setting__label">
                            <strong>{field.label}</strong>
                            {showTechnicalNames && <code>{field.key}</code>}
                            {field.restartRequired && <small>重启后生效</small>}
                          </span>
                          <small className="runtime-setting__description">{field.description}</small>
                          {field.type === 'boolean' ? (
                            <>
                              <span className="runtime-setting__switch">
                                <input
                                  checked={(values[field.key] ?? 'false') === 'true'}
                                  disabled={Boolean(savingGroup)}
                                  type="checkbox"
                                  onChange={(event) => changeValue(field.key, event.target.checked ? 'true' : 'false')}
                                />
                                <span>{(values[field.key] ?? 'false') === 'true' ? '已开启' : '已关闭'}</span>
                              </span>
                              {impact && (
                                <span className="runtime-setting__impact">
                                  <small>{impact.enabled}</small>
                                  <small>{impact.disabled}</small>
                                  <small>{impact.applies}</small>
                                </span>
                              )}
                            </>
                          ) : (
                            <span className="runtime-setting__control">
                              <input
                                disabled={markedForClear || Boolean(savingGroup)}
                                inputMode={field.type === 'number' ? 'numeric' : undefined}
                                placeholder={field.secret && field.hasValue ? '已保存，留空保持原值' : '未设置'}
                                type={field.secret && !visibleSecrets.has(field.key) ? 'password' : field.type === 'number' ? 'number' : field.type === 'url' ? 'url' : 'text'}
                                value={values[field.key] ?? ''}
                                onChange={(event) => changeValue(field.key, event.target.value)}
                              />
                              {field.secret && (
                                <button
                                  aria-label={visibleSecrets.has(field.key) ? `隐藏${field.label}` : `显示${field.label}`}
                                  className="runtime-setting__icon-button"
                                  disabled={Boolean(savingGroup)}
                                  title={visibleSecrets.has(field.key) ? '隐藏输入' : '显示输入'}
                                  type="button"
                                  onClick={() => toggleSecretVisibility(field.key)}
                                >
                                  {visibleSecrets.has(field.key) ? <EyeOff size={15} /> : <Eye size={15} />}
                                </button>
                              )}
                            </span>
                          )}
                          {field.secret && field.hasValue && (
                            <span className="runtime-setting__secret-state">
                              <span>已保存</span>
                              <input
                                aria-label={`清除${field.label}`}
                                checked={markedForClear}
                                disabled={Boolean(savingGroup)}
                                type="checkbox"
                                onChange={() => toggleClearSecret(field.key)}
                              />
                              <span>清除</span>
                            </span>
                          )}
                        </label>
                      );
                    })}
                  </div>
                  <footer className="runtime-settings__group-foot">
                    <span className={messages[group.id]?.includes('失败') ? 'is-error' : ''}>{messages[group.id] || (groupDirty ? '有未保存修改' : '配置已同步')}</span>
                    <button
                      className="ops-action-button ops-action-button--primary"
                      disabled={!groupDirty || Boolean(savingGroup)}
                      type="button"
                      onClick={() => saveGroup(group)}
                    >
                      <Save aria-hidden="true" size={14} />
                      {savingGroup === group.id ? '保存中…' : `保存${group.title}`}
                    </button>
                  </footer>
                </div>
              )}
            </section>
          );
        })}
        {visibleGroups.length === 0 && <p className="runtime-settings__empty">没有匹配的配置项</p>}
      </div>
    </article>
  );
}
