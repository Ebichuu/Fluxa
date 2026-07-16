import { useEffect, useState } from 'react';
import { Cloud, CloudOff, Database, KeyRound, LogOut, RefreshCcw, RotateCcw, Save, Settings2, ShieldCheck, Timer } from 'lucide-react';
import {
  clearSubscriptions,
  getHdhiveAuthorization,
  getAuthSession,
  getIntegrationSummary,
  getSubscriptionConfig,
  getTelegramChannels,
  logoutAuthSession,
  logoutTelegram,
  probeCloud115,
  runHdhiveCheckin,
  saveSubscriptionConfig,
  saveTelegramChannels,
  sendTelegramLoginCode,
  signInTelegram,
  type AuthSessionResponse
} from '../../services/api';
import type { SubscriptionHubConfig } from '../../types/subscriptions';
import type { IntegrationSummary } from '../../types/integrations';

const subscriptionModes = [
  { key: 'torra', label: 'PT / Torra 主通道', note: '默认策略：先由 Torra 搜索，再交给 qB 下载' },
  { key: 'moviepilot', label: 'MoviePilot 兼容通道', note: '保留 NasEmby 原 MoviePilot 推送能力' },
  { key: 'symedia', label: 'Symedia 兼容通道', note: '保留 NasEmby 原 Symedia 推送能力' }
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
  },
  {
    title: '115 与资源来源',
    note: '115 · Telegram · HDHive / pansou',
    fields: [
      ['ENV_115_COOKIES', '115 Cookie'],
      ['ENV_UPLOAD_PID', '115 目标目录'],
      ['ENV_TG_API_ID', 'Telegram API ID'],
      ['ENV_TG_API_HASH', 'Telegram API Hash'],
      ['ENV_HDHIVE_CHECKIN_ENABLED', 'HDHive 自动签到']
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
  const cloud = config.cloud_acquisition ?? {
    enabled: false,
    auto_fallback_enabled: false,
    manual_actions_enabled: false,
    wait_minutes: 360,
    sources: ['telegram', 'hdhive'] as Array<'telegram' | 'hdhive' | 'pansou'>,
    auto_select: false,
    policy_version: 1
  };
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
  const patchCloud = (changes: Partial<typeof cloud>) => {
    const next = { ...cloud, ...changes };
    if (!next.enabled) {
      next.auto_fallback_enabled = false;
      next.manual_actions_enabled = false;
      next.auto_select = false;
    }
    if (!next.auto_fallback_enabled) next.auto_select = false;
    setConfig({ ...config, cloud_acquisition: next });
  };
  const toggleCloudSource = (source: 'telegram' | 'hdhive' | 'pansou') => {
    patchCloud({
      sources: cloud.sources.includes(source)
        ? cloud.sources.filter((item) => item !== source)
        : [...cloud.sources, source]
    });
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
        <span>PT 主通道（NasEmby 原 provider 仍保留为兼容能力）</span>
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

      <div className="sub-config__row sub-config__cloud">
        <div className="sub-config__rule-head">
          <label>
            <input checked={cloud.enabled} type="checkbox" onChange={(event) => patchCloud({ enabled: event.target.checked })} />
            <Cloud size={14} />允许网盘第二通道
          </label>
          <strong>{cloud.enabled ? '已允许' : '默认关闭'}</strong>
        </div>
        <small>PT / Torra 始终先运行；只有满足等待、失败和重复检查条件后，网盘才可能接手。</small>
        <div className="sub-config__toggles">
          <label>
            <input
              checked={cloud.manual_actions_enabled}
              disabled={!cloud.enabled}
              type="checkbox"
              onChange={(event) => patchCloud({ manual_actions_enabled: event.target.checked })}
            />
            人工候选与单条转存
          </label>
          <label>
            <input
              checked={cloud.auto_fallback_enabled}
              disabled={!cloud.enabled}
              type="checkbox"
              onChange={(event) => patchCloud({ auto_fallback_enabled: event.target.checked })}
            />
            自动兜底
          </label>
          <label>
            <input
              checked={cloud.auto_select}
              disabled={!cloud.enabled || !cloud.auto_fallback_enabled}
              type="checkbox"
              onChange={(event) => patchCloud({ auto_select: event.target.checked })}
            />
            自动选择候选
          </label>
          <label className="sub-config__time">
            <Timer size={13} />PT 等待
            <input
              disabled={!cloud.enabled}
              max={10080}
              min={30}
              step={30}
              type="number"
              value={cloud.wait_minutes}
              onChange={(event) => patchCloud({ wait_minutes: Number(event.target.value) })}
            />
            分钟
          </label>
        </div>
        <div className="sub-config__sources" aria-label="网盘候选来源">
          {([['telegram', 'Telegram'], ['hdhive', 'HDHive'], ['pansou', 'pansou']] as const).map(([key, label]) => (
            <label key={key}>
              <input checked={cloud.sources.includes(key)} disabled={!cloud.enabled} type="checkbox" onChange={() => toggleCloudSource(key)} />
              {label}
            </label>
          ))}
        </div>
      </div>

      <div className="sub-config__row sub-config__rules">
        <div className="sub-config__rule-head">
          <label><input checked={rules.enabled} type="checkbox" onChange={(event) => patchRules({ enabled: event.target.checked })} />精准转存</label>
          <label>每次最多<input max={50} min={1} type="number" value={rules.max_per_run} onChange={(event) => patchRules({ max_per_run: Number(event.target.value) })} /></label>
        </div>
        <small>资源规则只用于网盘候选选择；关闭网盘通道时不会执行搜索或转存。</small>
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
  const [integrations, setIntegrations] = useState<IntegrationSummary | null>(null);
  const [integrationError, setIntegrationError] = useState('');
  const [probingIntegrations, setProbingIntegrations] = useState(false);
  const [integrationAction, setIntegrationAction] = useState('');
  const [integrationMessage, setIntegrationMessage] = useState('');
  const [telegramLogin, setTelegramLogin] = useState({ phone: '', api_id: '', api_hash: '', code: '' });
  const [telegramChannels, setTelegramChannels] = useState('');

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

  const loadIntegrations = (probe = false) => {
    setProbingIntegrations(probe);
    setIntegrationError('');
    getIntegrationSummary(probe)
      .then(setIntegrations)
      .catch((error: unknown) => setIntegrationError(error instanceof Error ? error.message : '集成状态读取失败'))
      .finally(() => setProbingIntegrations(false));
  };

  useEffect(() => {
    loadIntegrations(false);
    getTelegramChannels()
      .then((payload) => setTelegramChannels(payload.channels.map((channel) => channel.input).filter(Boolean).join('\n')))
      .catch(() => undefined);
  }, []);

  const runIntegrationAction = async (key: string, action: () => Promise<string>) => {
    setIntegrationAction(key);
    setIntegrationMessage('');
    try {
      setIntegrationMessage(await action());
      loadIntegrations(false);
    } catch (error) {
      setIntegrationMessage(error instanceof Error ? error.message : '操作失败');
    } finally {
      setIntegrationAction('');
    }
  };

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
          <h1>在一个页面管理服务连接，但不在浏览器持久保存敏感凭据。</h1>
          <p className="ops-deck">PT 是默认获取通道；自动云盘兜底保持关闭，人工补资源不受影响。</p>
        </div>
        <div className="ops-settings-guard">
          <span><KeyRound size={15} />凭据策略</span>
          <strong>服务端安全保存</strong>
          <small>已保存的账号、密码与 Token 不回填前端</small>
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
            <div><CloudOff size={16} /><span><strong>自动云盘兜底</strong><small>独立开关、证据链与单条动作闸门已接入</small></span></div>
            <b>关闭</b>
          </div>
          <p className="ops-settings-note">关闭自动兜底不会影响人工资源搜索、确认转存或手动补资源。</p>
        </article>

        <article className="ops-settings-card ops-settings-card--wide">
          <header className="ops-settings-card__head">
            <div><span><Cloud size={16} /></span><div><small>NASEMBY INTEGRATIONS</small><h2>网盘与兼容服务</h2></div></div>
            <button className="tool-link" disabled={probingIntegrations || !integrations?.probeEnabled} type="button" onClick={() => loadIntegrations(true)}>
              <RefreshCcw aria-hidden="true" size={14} />
              {probingIntegrations ? '检查中' : '检查连接'}
            </button>
          </header>
          <div className="ops-connection-grid">
            {(integrations?.services ?? []).map((service) => (
              <section className="ops-connection-group" key={service.id}>
                <div className="ops-connection-group__head">
                  <h3>{service.name}</h3>
                  <p>{service.role}</p>
                </div>
                <div className="ops-policy-row">
                  <span><strong>{service.configured ? '已配置' : '未配置'}</strong><small>{service.detail}</small></span>
                  <b>{service.connected === true ? '在线' : service.connected === false ? '不可用' : '未检查'}</b>
                </div>
              </section>
            ))}
          </div>
          {!integrations && !integrationError && <p className="ops-settings-note">正在读取 NasEmby 集成状态…</p>}
          {integrationError && <p className="ops-access-error" role="alert">{integrationError}</p>}
          {integrations && !integrations.probeEnabled && <p className="ops-settings-note">连接检查默认关闭；fnOS 配置 MCC_INTEGRATION_PROBE_ENABLED 后可人工检查。</p>}
          <div className="sub-config__row sub-config__row--pair ops-integration-actions">
            <section>
              <h3>115 账号</h3>
              <p>只读取账号状态，不把 Cookie 返回浏览器。</p>
              <button
                className="tool-link"
                disabled={Boolean(integrationAction) || !integrations?.probeEnabled}
                type="button"
                onClick={() => runIntegrationAction('115', async () => (await probeCloud115()).ok ? '115 账号检查通过' : '115 账号不可用')}
              >
                <RefreshCcw size={14} />{integrationAction === '115' ? '检查中' : '检查 115 账号'}
              </button>
            </section>
            <section>
              <h3>HDHive / pansou</h3>
              <p>授权与签到分别受管理开关和一小时冷却保护。</p>
              <div className="sub-config__foot">
                <button
                  className="tool-link"
                  disabled={Boolean(integrationAction)}
                  type="button"
                  onClick={() => runIntegrationAction('hdhive-auth', async () => {
                    const payload = await getHdhiveAuthorization();
                    window.open(payload.authorizationUrl, '_blank', 'noopener,noreferrer');
                    return '已打开 HDHive 授权页';
                  })}
                >
                  <KeyRound size={14} />获取授权
                </button>
                <button
                  className="tool-link"
                  disabled={Boolean(integrationAction) || !integrations?.managementEnabled}
                  type="button"
                  onClick={() => runIntegrationAction('hdhive-checkin', async () => (await runHdhiveCheckin()).message)}
                >
                  <RefreshCcw size={14} />{integrationAction === 'hdhive-checkin' ? '执行中' : '立即签到'}
                </button>
              </div>
            </section>
          </div>
          <div className="sub-config__row ops-integration-telegram">
            <div className="sub-config__rule-head"><strong>Telegram 登录与频道</strong><small>输入只发送到当前同源 Python 后端，不写入浏览器存储。</small></div>
            <div className="sub-config__row sub-config__row--pair">
              <label>手机号<input autoComplete="tel" placeholder="+8618000000000" type="tel" value={telegramLogin.phone} onChange={(event) => setTelegramLogin({ ...telegramLogin, phone: event.target.value })} /></label>
              <label>API ID<input inputMode="numeric" type="text" value={telegramLogin.api_id} onChange={(event) => setTelegramLogin({ ...telegramLogin, api_id: event.target.value })} /></label>
              <label>API Hash<input autoComplete="off" type="password" value={telegramLogin.api_hash} onChange={(event) => setTelegramLogin({ ...telegramLogin, api_hash: event.target.value })} /></label>
              <label>验证码<input autoComplete="one-time-code" inputMode="numeric" type="text" value={telegramLogin.code} onChange={(event) => setTelegramLogin({ ...telegramLogin, code: event.target.value })} /></label>
            </div>
            <label>
              资源频道（每行一个用户名、链接或频道 ID）
              <textarea rows={3} value={telegramChannels} onChange={(event) => setTelegramChannels(event.target.value)} />
            </label>
            <div className="sub-config__foot">
              <button
                className="tool-link"
                disabled={Boolean(integrationAction) || !integrations?.managementEnabled}
                type="button"
                onClick={() => runIntegrationAction('telegram-code', async () => {
                  const payload = await sendTelegramLoginCode(telegramLogin);
                  setTelegramLogin({ ...telegramLogin, api_hash: '' });
                  return payload.message;
                })}
              >发送验证码</button>
              <button
                className="tool-link"
                disabled={Boolean(integrationAction) || !integrations?.managementEnabled}
                type="button"
                onClick={() => runIntegrationAction('telegram-login', async () => (await signInTelegram(telegramLogin.code)).authorized ? 'Telegram 登录成功' : 'Telegram 登录未完成')}
              >完成登录</button>
              <button
                className="tool-link"
                disabled={Boolean(integrationAction) || !integrations?.managementEnabled}
                type="button"
                onClick={() => runIntegrationAction('telegram-channels', async () => {
                  const rows = telegramChannels.split(/\r?\n/).map((input) => input.trim()).filter(Boolean).map((input) => ({ input }));
                  const payload = await saveTelegramChannels(rows);
                  return `已保存 ${payload.channelCount} 个 Telegram 频道`;
                })}
              >保存频道</button>
              <button
                className="tool-link"
                disabled={Boolean(integrationAction) || !integrations?.managementEnabled}
                type="button"
                onClick={() => runIntegrationAction('telegram-logout', async () => { await logoutTelegram(); return 'Telegram 已退出'; })}
              >退出 Telegram</button>
            </div>
          </div>
          {integrationMessage && <p className="ops-settings-note" role="status">{integrationMessage}</p>}
          {integrations && !integrations.managementEnabled && <p className="ops-settings-note">敏感管理动作默认禁用；fnOS 需同时开启总管理开关和对应服务细分开关。</p>}
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
