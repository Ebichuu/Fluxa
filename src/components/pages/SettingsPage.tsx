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
import { ConfirmDialog } from '../layout/ConfirmDialog';
import { RuntimeSettingsPanel } from './RuntimeSettingsPanel';

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

interface SubscriptionHubSettingsProps {
  onModeChange?: (label: string) => void;
}

export function SubscriptionHubSettings({ onModeChange }: SubscriptionHubSettingsProps) {
  const [config, setConfig] = useState<SubscriptionHubConfig | null>(null);
  const [sources, setSources] = useState<Array<{ key: string; label: string; mediaType: 'movie' | 'tv' }>>([]);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [clearDialogOpen, setClearDialogOpen] = useState(false);
  const [clearPhrase, setClearPhrase] = useState('');

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

  const openClearDialog = () => {
    setClearPhrase('');
    setClearDialogOpen(true);
  };

  const clearAll = () => {
    if (clearPhrase !== '清空全部订阅') return;
    setClearDialogOpen(false);
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
        <button className="tool-link tool-link--danger" disabled={saving} type="button" onClick={openClearDialog}>
          清空全部订阅
        </button>
      </div>
      <ConfirmDialog
        busy={saving}
        labelledBy="clear-subscriptions-title"
        describedBy="clear-subscriptions-description"
        open={clearDialogOpen}
        onClose={() => setClearDialogOpen(false)}
      >
        <span className="ops-confirm-dialog__signal ops-confirm-dialog__signal--danger">高风险操作</span>
        <h2 id="clear-subscriptions-title">清空全部订阅？</h2>
        <p id="clear-subscriptions-description">这会删除 NasEmby 订阅台账。输入指定短语后才能继续，来源配置不会被删除。</p>
        <label className="ops-confirm-dialog__input">
          输入“清空全部订阅”
          <input
            autoComplete="off"
            data-dialog-initial-focus
            value={clearPhrase}
            onChange={(event) => setClearPhrase(event.target.value)}
          />
        </label>
        <div className="ops-confirm-dialog__actions">
          <button className="ops-action-button" disabled={saving} type="button" onClick={() => setClearDialogOpen(false)}>取消</button>
          <button className="ops-action-button ops-action-button--danger" disabled={clearPhrase !== '清空全部订阅' || saving} type="button" onClick={clearAll}>确认清空</button>
        </div>
      </ConfirmDialog>
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
          <p className="ops-eyebrow">连接与安全</p>
          <h1>设置</h1>
          <p className="ops-page-subtitle">管理软件连接、功能开关与访问保护。</p>
          <p className="ops-deck">所有应用配置都可在这里修改；密码、Token 与 Cookie 只写入服务端，不回显明文。</p>
        </div>
        <div className="ops-settings-guard">
          <span><KeyRound size={15} />凭据策略</span>
          <strong>服务端安全保存</strong>
          <small>已保存的账号、密码与访问令牌不回填前端</small>
        </div>
      </section>

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

        <RuntimeSettingsPanel />

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
                  : accessSession?.enabled ? '当前会话不可用' : '管理员认证未启用'}
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
