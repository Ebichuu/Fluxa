import { useEffect, useState } from 'react';
import { Database, KeyRound, LogOut, RotateCcw, Save, Settings2, ShieldCheck } from 'lucide-react';
import {
  clearSubscriptions,
  getAuthSession,
  getSubscriptionConfig,
  logoutAuthSession,
  saveSubscriptionConfig,
  type AuthSessionResponse
} from '../../services/api';
import type { SubscriptionHubConfig } from '../../types/subscriptions';
import type { HealthResponse } from '../../types/media';
import { PageStatusHeader } from '../layout/PageStatusHeader';

interface SettingsPageProps {
  health: HealthResponse | null;
}

const subscriptionModes = [
  { key: 'torra', label: 'PT / Torra 主通道', note: 'Torra 负责 PT 搜索、qB 编排和秒传到 115' }
] as const;

const subscriptionSourceGroups = [
  { key: 'movie', label: '电影', sources: ['hot_movie', 'movie_realtime', 'showing'] },
  { key: 'tv', label: '剧集', sources: ['hot_tv', 'tv_realtime'] },
  { key: 'extra', label: '剧集榜单', sources: ['global_tv', 'daily_airing', 'domestic_tv', 'japanese_tv', 'korean_tv', 'american_tv', 'anime_tv'] },
  { key: 'platform', label: '平台热更', sources: ['platform_tencent', 'platform_youku', 'platform_iqiyi', 'platform_mango'] }
] as const;

const latestSubscriptionSources = subscriptionSourceGroups.flatMap((group) => [...group.sources]);

const connectionGroups: Array<{
  title: string;
  note: string;
  fields: Array<[string, string]>;
}> = [
  {
    title: 'Emby',
    note: '首页数据源 · 媒体库与图片（API Key 或账号密码二选一）',
    fields: [
      ['EMBY_BASE_URL', 'Emby 服务地址'],
      ['EMBY_API_KEY', 'Emby API Key'],
      ['EMBY_USER_ID', 'Emby 用户 ID'],
      ['EMBY_USERNAME', 'Emby 用户名（密码模式）'],
      ['EMBY_PASSWORD', 'Emby 密码（密码模式）']
    ]
  },
  {
    title: '下载与来源',
    note: 'qBittorrent · Torra',
    fields: [
      ['QB_BASE_URL', 'qBittorrent 地址'],
      ['QB_USERNAME', 'qBittorrent 用户名'],
      ['QB_PASSWORD', 'qBittorrent 密码'],
      ['TORRA_BASE_URL', 'Torra 地址'],
      ['TORRA_TOKEN', 'Torra Token（订阅推送用）']
    ]
  },
  {
    title: '工具链',
    note: 'Symedia · 入库记录读取（Token 或账号密码二选一）',
    fields: [
      ['SYMEDIA_BASE_URL', 'Symedia 地址'],
      ['SYMEDIA_TOKEN', 'Symedia Token'],
      ['SYMEDIA_USERNAME', 'Symedia 用户名（密码模式）'],
      ['SYMEDIA_PASSWORD', 'Symedia 密码（密码模式）']
    ]
  },
  {
    title: '自动订阅（内置）',
    note: '追剧日历与自动订阅 · TMDB 数据源',
    fields: [
      ['TMDB_API_KEY', 'TMDB API Key']
    ]
  },
  {
    title: 'PT 兼容能力',
    note: 'MoviePilot 作为可选兼容通道，不改变 Torra 默认优先级',
    fields: [
      ['ENV_MOVIEPILOT_URL', 'MoviePilot 地址'],
      ['ENV_MOVIEPILOT_API_TOKEN', 'MoviePilot Token']
    ]
  }
];

interface SubscriptionHubSettingsProps {
  onModeChange?: (label: string) => void;
}

export function SubscriptionHubSettings({ onModeChange }: SubscriptionHubSettingsProps) {
  const [config, setConfig] = useState<SubscriptionHubConfig | null>(null);
  const [sources, setSources] = useState<Array<{ key: string; label: string; mediaType: 'movie' | 'tv' }>>([]);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSubscriptionConfig()
      .then((payload) => {
        if (!cancelled && payload.success) {
          setConfig(payload.config);
          setSources(payload.sources ?? []);
          const mode = subscriptionModes.find((item) => item.key === payload.config.mode);
          onModeChange?.(mode?.label ?? payload.config.mode);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMessage('订阅配置加载失败');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [onModeChange]);

  if (!config) {
    return <div className="ops-settings-card ops-settings-card--wide ops-empty">{message || '订阅配置加载中…'}</div>;
  }

  const douban = config.douban;
  const currentModeLabel = subscriptionModes.find((mode) => mode.key === config.mode)?.label ?? config.mode;
  const patch = (changes: Partial<SubscriptionHubConfig['douban']>) => {
    setConfig({ ...config, douban: { ...douban, ...changes } });
  };
  const toggleSource = (key: string) => {
    patch({
      sources: douban.sources.includes(key)
        ? douban.sources.filter((row) => row !== key)
        : [...douban.sources, key]
    });
  };

  const save = () => {
    setSaving(true);
    setMessage('');
    saveSubscriptionConfig(config)
      .then((payload) => {
        if (payload.success) {
          setConfig(payload.config);
          setMessage('已保存');
        } else {
          setMessage(payload.error || '保存失败');
        }
      })
      .catch(() => setMessage('保存失败'))
      .finally(() => setSaving(false));
  };

  const clearAll = () => {
    const confirmation = window.prompt('这是高风险操作。请输入“清空全部订阅”继续：');
    if (confirmation !== '清空全部订阅') return;
    setSaving(true);
    setMessage('');
    clearSubscriptions()
      .then(() => setMessage('已清空 NasEmby 订阅列表'))
      .catch((error: unknown) => setMessage(error instanceof Error ? error.message : '清空失败'))
      .finally(() => setSaving(false));
  };

  const applyLatestPreset = () => {
    const year = new Date().getFullYear();
    setConfig({
      ...config,
      douban: {
        ...douban,
        sources: [...latestSubscriptionSources],
        movie_years: [String(year), String(year - 1), String(year - 2)],
        tv_min_rating: 0,
        task_time: douban.task_time || '08:30'
      }
    });
    setMessage('已套用最新规则，保存后生效');
  };

  return (
    <div className="ops-settings-card ops-settings-card--wide sub-config">
      <header className="ops-settings-card__head">
        <div><span><Database size={16} /></span><div><small>自动订阅</small><h2>订阅扫描与来源</h2></div></div>
        <strong>当前配置：{currentModeLabel}</strong>
      </header>
      <div className="sub-config__toggles">
        <label>
          <input checked={douban.enabled} type="checkbox" onChange={(event) => patch({ enabled: event.target.checked })} />
          启用自动订阅
        </label>
        <label>
          <input checked={douban.movie_enabled} type="checkbox" onChange={(event) => patch({ movie_enabled: event.target.checked })} />
          电影
        </label>
        <label>
          <input checked={douban.tv_enabled} type="checkbox" onChange={(event) => patch({ tv_enabled: event.target.checked })} />
          剧集
        </label>
        <label>
          <input checked={douban.task_enabled} type="checkbox" onChange={(event) => patch({ task_enabled: event.target.checked })} />
          每日定时任务
        </label>
        <label className="sub-config__time">
          任务时间
          <input
            type="time"
            value={douban.task_time}
            onChange={(event) => patch({ task_time: event.target.value })}
          />
        </label>
      </div>

      <div className="sub-config__row">
        <span>订阅来源</span>
        <div className="sub-config__source-groups">
          {subscriptionSourceGroups.map((group) => (
            <fieldset key={group.key}>
              <legend>{group.label}</legend>
              <div className="sub-config__sources">
                {group.sources.map((sourceKey) => {
                  const source = sources.find((item) => item.key === sourceKey);
                  if (!source) return null;
                  return (
                    <label key={source.key}>
                      <input checked={douban.sources.includes(source.key)} type="checkbox" onChange={() => toggleSource(source.key)} />
                      {source.label}
                    </label>
                  );
                })}
              </div>
            </fieldset>
          ))}
        </div>
      </div>

      <div className="sub-config__row sub-config__row--pair">
        <label>
          电影年份（逗号分隔）
          <input
            type="text"
            value={douban.movie_years.join(', ')}
            onChange={(event) => patch({ movie_years: event.target.value.split(/[\s,，]+/).filter(Boolean) })}
          />
        </label>
        <label>
          剧集最低评分（0 不限）
          <input
            max={10}
            min={0}
            step={0.1}
            type="number"
            value={douban.tv_min_rating}
            onChange={(event) => patch({ tv_min_rating: Number(event.target.value) })}
          />
        </label>
      </div>

      <div className="sub-config__row">
        <span>PT 主通道（旧资源获取方式仅保留兼容）</span>
        <div className="sub-config__modes">
          {subscriptionModes.map((mode) => (
            <label className={config.mode === mode.key ? 'is-active' : undefined} key={mode.key}>
              <input
                checked={config.mode === mode.key}
                name="subscription-mode"
                type="radio"
                value={mode.key}
                onChange={() => {
                  setConfig({ ...config, mode: mode.key });
                  onModeChange?.(mode.label);
                }}
              />
              <strong>{mode.label}</strong>
              <small>{mode.note}</small>
            </label>
          ))}
        </div>
      </div>

      <div className="sub-config__row">
        <label>
          排除 / 屏蔽标题（换行或逗号分隔；订阅列表里"屏蔽"的条目也会加进来）
          <textarea
            rows={3}
            value={douban.exclude_titles.join('\n')}
            onChange={(event) => patch({ exclude_titles: event.target.value.split(/[\n,，;；|]+/).map((row) => row.trim()).filter(Boolean) })}
          />
        </label>
      </div>

      <div className="sub-config__foot">
        <button className="tool-link" disabled={saving} type="button" onClick={applyLatestPreset}>
          <RotateCcw aria-hidden="true" size={14} />
          使用最新规则
        </button>
        <button className="tool-link" disabled title="实机测试阶段开放；该动作会写入订阅并可能排队后处理" type="button">
          同步全球日播
        </button>
        <button className="tool-link" disabled={saving} type="button" onClick={save}>
          <Save aria-hidden="true" size={14} />
          {saving ? '保存中…' : '保存订阅配置'}
        </button>
        {message && <small>{message}</small>}
        {douban.last_run_at && <small>上次运行：{douban.last_run_at}</small>}
        <button className="tool-link" disabled={saving} type="button" onClick={clearAll}>
          清空全部订阅
        </button>
      </div>
    </div>
  );
}

export function SettingsPage({ health }: SettingsPageProps) {
  const [accessSession, setAccessSession] = useState<AuthSessionResponse | null>(null);
  const [accessError, setAccessError] = useState('');
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAuthSession()
      .then((session) => {
        if (!cancelled) setAccessSession(session);
      })
      .catch(() => {
        if (!cancelled) setAccessError('访问状态读取失败');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const logout = () => {
    setLoggingOut(true);
    setAccessError('');
    logoutAuthSession()
      .then(() => window.location.assign('/auth/login'))
      .catch((error: unknown) => {
        setAccessError(error instanceof Error ? error.message : '退出失败');
        setLoggingOut(false);
      });
  };

  const configuredCount = health?.services.filter((service) => service.configured).length ?? 0;
  const serviceCount = health?.services.length ?? 10;
  const accessStatus = accessSession
    ? accessSession.enabled ? accessSession.authenticated ? '访问保护已登录' : '访问保护需要登录' : '本地未启用访问密钥'
    : accessError ? '访问保护状态不可用' : '正在读取访问保护';

  return (
    <main className="work-page ops-page ops-page--settings">
      <PageStatusHeader
        context="连接与安全"
        detail={`${accessStatus} · 凭据由服务端保存`}
        status={health ? `${configuredCount} / ${serviceCount} 项服务已配置` : '服务配置状态暂不可用'}
        title="系统设置"
        tone={health && configuredCount > 0 ? 'ok' : 'neutral'}
      />

      <section className="ops-settings-grid">
        <article className="ops-settings-card ops-settings-policy">
          <header className="ops-settings-card__head">
            <div><span><Settings2 size={16} /></span><div><small>获取路线</small><h2>获取通道</h2></div></div>
          </header>
          <div className="ops-policy-row ops-policy-row--primary">
            <div><Database size={16} /><span><strong>PT / Torra</strong><small>订阅的默认自动获取通道</small></span></div>
            <b>始终优先</b>
          </div>
          <div className="ops-policy-row">
            <div><ShieldCheck size={16} /><span><strong>Torra → 115</strong><small>秒传由 Torra 独占，中控不启动第二套上传器</small></span></div>
            <b>单一路线</b>
          </div>
          <p className="ops-settings-note">Telegram 频道网盘订阅与自动兜底已延期，底层源码继续保留。</p>
        </article>

        <article className="ops-settings-card ops-settings-card--wide">
          <header className="ops-settings-card__head">
            <div><span><KeyRound size={16} /></span><div><small>只读配置</small><h2>服务连接</h2></div></div>
            <strong>由服务端环境变量提供</strong>
          </header>
          <div className="ops-connection-grid">
          {connectionGroups.map((group) => (
            <section className="ops-connection-group" key={group.title}>
              <div className="ops-connection-group__head">
                <h3>{group.title}</h3>
                <p>{group.note}</p>
              </div>
              <div className="ops-env-list">
                {group.fields.map(([key, label]) => (
                  <div className="ops-env-row" key={key}>
                    <span>{label}</span>
                    <code>{key}</code>
                    <i aria-hidden="true" />
                  </div>
                ))}
              </div>
            </section>
          ))}
          </div>
        </article>

        <article className="ops-settings-card ops-settings-card--wide ops-access-card">
          <header className="ops-settings-card__head">
            <div><span><ShieldCheck size={16} /></span><div><small>私人访问</small><h2>访问保护</h2></div></div>
            <strong>
              {accessSession
                ? accessSession.enabled ? accessSession.authenticated ? '已登录' : '需要登录' : '本地未启用'
                : accessError ? '状态不可用' : '读取中'}
            </strong>
          </header>
          <div className="ops-access-row">
            <div>
              <strong>{accessSession?.enabled ? '签名会话' : '开发环境'}</strong>
              <small>
                {accessSession?.expiresAt
                  ? `有效至 ${new Date(accessSession.expiresAt).toLocaleString('zh-CN', { hour12: false })}`
                  : accessSession?.enabled ? '当前会话不可用' : '未设置访问密钥'}
              </small>
            </div>
            {accessSession?.enabled && accessSession.authenticated && (
              <button className="tool-link ops-access-logout" disabled={loggingOut} type="button" onClick={logout}>
                <LogOut aria-hidden="true" size={14} />
                {loggingOut ? '正在退出' : '退出登录'}
              </button>
            )}
          </div>
          {accessError && <p className="ops-access-error" role="alert">{accessError}</p>}
        </article>

      </section>
    </main>
  );
}
