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
import { getRuntimeSettings, saveRuntimeSettings } from '../../services/api';
import type {
  RuntimeSettingField,
  RuntimeSettingGroup,
  RuntimeSettingsResponse
} from '../../types/runtimeSettings';

const initiallyOpen = new Set(['emby', 'qbittorrent', 'torra', 'symedia', 'tmdb', 'automation']);

function valuesFrom(payload: RuntimeSettingsResponse) {
  return Object.fromEntries(
    payload.groups.flatMap((group) => group.fields.map((field) => [field.key, field.value]))
  );
}

function fieldMatches(field: RuntimeSettingField, query: string) {
  const haystack = `${field.label} ${field.description} ${field.key}`.toLocaleLowerCase('zh-CN');
  return haystack.includes(query);
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

  const load = () => {
    setError('');
    getRuntimeSettings()
      .then((next) => {
        setPayload(next);
        setValues(valuesFrom(next));
        setDirty(new Set());
        setClearSecrets(new Set());
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '配置加载失败'));
  };

  useEffect(load, []);

  const normalisedQuery = query.trim().toLocaleLowerCase('zh-CN');
  const visibleGroups = useMemo(() => {
    if (!payload) return [];
    if (!normalisedQuery) return payload.groups;
    return payload.groups
      .map((group) => ({
        ...group,
        fields: group.fields.filter((field) => fieldMatches(field, normalisedQuery))
      }))
      .filter((group) => group.fields.length > 0);
  }, [normalisedQuery, payload]);

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
      .then((next) => {
        setPayload(next);
        setValues(valuesFrom(next));
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
        const restart = next.restartRequired?.length
          ? `已保存，${next.restartRequired.length} 项重启后生效`
          : '已保存并应用';
        setMessages((current) => ({ ...current, [group.id]: restart }));
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
        {error && <button className="tool-link" type="button" onClick={load}><RotateCcw size={14} />重试</button>}
      </article>
    );
  }

  return (
    <article className="ops-settings-card ops-settings-card--wide runtime-settings">
      <header className="ops-settings-card__head runtime-settings__head">
        <div><span><Settings2 size={16} /></span><div><small>管理员配置</small><h2>软件连接与功能开关</h2></div></div>
        <strong>{payload.groups.reduce((count, group) => count + group.fields.length, 0)} 项可编辑</strong>
      </header>

      <div className="runtime-settings__toolbar">
        <label className="runtime-settings__search">
          <Search aria-hidden="true" size={15} />
          <input
            aria-label="搜索配置项"
            placeholder="搜索软件、配置名称或环境变量"
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
                      return (
                        <label className={`runtime-setting runtime-setting--${field.type}`} key={field.key}>
                          <span className="runtime-setting__label">
                            <strong>{field.label}</strong>
                            {showTechnicalNames && <code>{field.key}</code>}
                            {field.restartRequired && <small>重启后生效</small>}
                          </span>
                          <small className="runtime-setting__description">{field.description}</small>
                          {field.type === 'boolean' ? (
                            <span className="runtime-setting__switch">
                              <input
                                checked={(values[field.key] ?? 'false') === 'true'}
                                type="checkbox"
                                onChange={(event) => changeValue(field.key, event.target.checked ? 'true' : 'false')}
                              />
                              <span>{(values[field.key] ?? 'false') === 'true' ? '已开启' : '已关闭'}</span>
                            </span>
                          ) : (
                            <span className="runtime-setting__control">
                              <input
                                disabled={markedForClear}
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
                      className="tool-link"
                      disabled={!groupDirty || savingGroup === group.id}
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
