import { useEffect, useState } from 'react';
import { CloudOff, Database, KeyRound, LogOut, RotateCcw, Save, Settings2, ShieldCheck } from 'lucide-react';
import {
  clearSubscriptions,
  getAuthSession,
  getSubscriptionConfig,
  logoutAuthSession,
  saveSubscriptionConfig,
  type AuthSessionResponse
} from '../../services/api';
import type { SubscriptionHubConfig } from '../../types/subscriptions';

const subscriptionModes = [
  { key: 'moviepilot', label: '模式 1 · MoviePilot', note: '直接推送到 MoviePilot 订阅' },
  { key: 'torra', label: '模式 2 · Torra', note: 'PT 优先，直接推送到 Torra 订阅' },
  { key: 'resource', label: '模式 3 · 资源转存', note: '搜索频道与影巢资源并按规则转存' },
  { key: 'resource_then_pt', label: '模式 4 · 资源优先，PT 兜底', note: '有资源则转存，无资源再转推 PT' },
  { key: 'symedia', label: '模式 5 · Symedia', note: '推送到 Symedia 并触发搜索' }
] as const;

const subscriptionSourceGroups = [
  { key: 'movie', label: '电影', sources: ['hot_movie', 'movie_realtime', 'showing'] },
  { key: 'tv', label: '剧集', sources: ['hot_tv', 'tv_realtime'] },
  { key: 'extra', label: '剧集榜单', sources: ['global_tv', 'daily_airing', 'domestic_tv', 'japanese_tv', 'korean_tv', 'american_tv', 'anime_tv'] },
  { key: 'platform', label: '平台热更', sources: ['platform_tencent', 'platform_youku', 'platform_iqiyi', 'platform_mango'] }
] as const;

const latestSubscriptionSources = subscriptionSourceGroups.flatMap((group) => [...group.sources]);

const resourceRuleGroups = [
  { key: 'resolution', label: '分辨率', values: [['4k', '4K'], ['1080p', '1080P'], ['720p_low', '720P 及以下']] },
  { key: 'color', label: '色彩', values: [['dv_hdr', 'DV & HDR'], ['dv', 'DV'], ['hdr10', 'HDR10'], ['hdr', 'HDR']] },
  { key: 'audio', label: '音频', values: [['truehd', 'TRUEHD'], ['dtshdma', 'DTS-HD MA'], ['dtsx', 'DTS-X'], ['dtshd', 'DTS-HD'], ['dts', 'DTS'], ['eac3', 'EAC3'], ['ac3', 'AC3'], ['flac', 'FLAC'], ['aac', 'AAC']] },
  { key: 'extension', label: '扩展名', values: [['mkv', 'MKV'], ['mp4', 'MP4'], ['ts', 'TS'], ['iso', 'ISO'], ['rmvb', 'RMVB'], ['avi', 'AVI'], ['mov', 'MOV'], ['mpeg', 'MPEG'], ['mpg', 'MPG'], ['wmv', 'WMV'], ['minor', '小众格式']] },
  { key: 'size', label: '文件体积', values: [['big_to_small', '由大到小'], ['ge40g', '40G 以上'], ['20_40g', '20-40G'], ['10_20g', '10-20G'], ['5_10g', '5-10G'], ['0_5g', '0-5G'], ['gt115g', '115G 以上']] }
] as const;

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
    title: '订阅中枢（内置）',
    note: '追剧日历与自动订阅 · TMDB 数据源',
    fields: [
      ['TMDB_API_KEY', 'TMDB API Key']
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
  const rules = config.resource_rules ?? {
    enabled: false,
    auto_transfer: true,
    max_per_run: 8,
    groups: {
      resolution: { require: ['4k'], reject: [] },
      color: { require: ['dv'], reject: [] },
      audio: { require: [], reject: [] },
      extension: { require: ['mkv'], reject: [] },
      size: { require: [], reject: [] },
      keyword: { require: [], reject: [] },
      exclude_keyword: { require: [], reject: [] }
    }
  };
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
  const patchRules = (changes: Partial<typeof rules>) => {
    setConfig({ ...config, resource_rules: { ...rules, ...changes } });
  };
  const cycleRule = (groupKey: string, value: string) => {
    const group = rules.groups[groupKey] ?? { require: [], reject: [] };
    const current = group.require.includes(value) ? 'require' : group.reject.includes(value) ? 'reject' : 'ignore';
    const next = current === 'ignore' ? 'require' : current === 'require' ? 'reject' : 'ignore';
    const require = group.require.filter((row) => row !== value);
    const reject = group.reject.filter((row) => row !== value);
    if (next === 'require') require.push(value);
    if (next === 'reject') reject.push(value);
    patchRules({ groups: { ...rules.groups, [groupKey]: { require, reject } } });
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
        <div><span><Database size={16} /></span><div><small>SUBSCRIPTION DEFAULTS</small><h2>订阅扫描与来源</h2></div></div>
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
        <span>订阅模式（来自 NasEmby 源码）</span>
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

      <div className="sub-config__row sub-config__rules">
        <div className="sub-config__rule-head">
          <label><input checked={rules.enabled} type="checkbox" onChange={(event) => patchRules({ enabled: event.target.checked })} />精准转存</label>
          <label>每次最多<input max={50} min={1} type="number" value={rules.max_per_run} onChange={(event) => patchRules({ max_per_run: Number(event.target.value) })} /></label>
        </div>
        <small>资源规则只在模式 3/4 使用；PT / Torra 模式不会调用云盘转存。</small>
        <div className="sub-config__rule-groups">
          {resourceRuleGroups.map((group) => (
            <fieldset key={group.key}>
              <legend>{group.label}</legend>
              {group.values.map(([value, label]) => {
                const rule = rules.groups[group.key] ?? { require: [], reject: [] };
                const state = rule.require.includes(value) ? 'require' : rule.reject.includes(value) ? 'reject' : 'ignore';
                return (
                  <button className={`sub-config__rule-chip is-${state}`} key={value} type="button" onClick={() => cycleRule(group.key, value)}>
                    <b>{state === 'require' ? '✓' : state === 'reject' ? '×' : '·'}</b>{label}
                  </button>
                );
              })}
            </fieldset>
          ))}
        </div>
        <div className="sub-config__row sub-config__row--pair">
          <label>
            必须包含关键词
            <input
              type="text"
              value={(rules.groups.keyword?.require ?? []).join(', ')}
              onChange={(event) => patchRules({ groups: { ...rules.groups, keyword: { require: event.target.value.split(/[,，]+/).map((row) => row.trim()).filter(Boolean), reject: [] } } })}
            />
          </label>
          <label>
            排除关键词
            <input
              type="text"
              value={(rules.groups.exclude_keyword?.require ?? []).join(', ')}
              onChange={(event) => patchRules({ groups: { ...rules.groups, exclude_keyword: { require: event.target.value.split(/[,，]+/).map((row) => row.trim()).filter(Boolean), reject: [] } } })}
            />
          </label>
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

export function SettingsPage() {
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

  return (
    <main className="work-page ops-page ops-page--settings">
      <section className="ops-hero ops-hero--settings">
        <div>
          <p className="ops-eyebrow">SETTINGS / CONTROL POLICY</p>
          <h1>设置只管理策略与显示，不把敏感凭据存进浏览器。</h1>
          <p className="ops-deck">PT 是默认获取通道；自动云盘兜底保持关闭，人工补资源不受影响。</p>
        </div>
        <div className="ops-settings-guard">
          <span><KeyRound size={15} />凭据策略</span>
          <strong>环境变量只读</strong>
          <small>账号、密码与 Token 不在前端保存</small>
        </div>
      </section>

      <section className="ops-settings-grid">
        <article className="ops-settings-card ops-settings-policy">
          <header className="ops-settings-card__head">
            <div><span><Settings2 size={16} /></span><div><small>ACQUISITION POLICY</small><h2>获取通道</h2></div></div>
          </header>
          <div className="ops-policy-row ops-policy-row--primary">
            <div><Database size={16} /><span><strong>PT / Torra</strong><small>订阅的默认自动获取通道</small></span></div>
            <b>始终优先</b>
          </div>
          <div className="ops-policy-row">
            <div><CloudOff size={16} /><span><strong>自动云盘兜底</strong><small>后端开关与证据链尚未接入</small></span></div>
            <b>关闭</b>
          </div>
          <p className="ops-settings-note">关闭自动兜底不会影响人工资源搜索、确认转存或手动补资源。</p>
        </article>

        <article className="ops-settings-card ops-settings-card--wide">
          <header className="ops-settings-card__head">
            <div><span><KeyRound size={16} /></span><div><small>READ-ONLY CONNECTIONS</small><h2>服务连接</h2></div></div>
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
            <div><span><ShieldCheck size={16} /></span><div><small>PRIVATE ACCESS</small><h2>访问保护</h2></div></div>
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
