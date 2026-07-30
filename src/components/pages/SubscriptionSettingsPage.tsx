import { useEffect, useState } from 'react';
import { ArrowLeft, Database, RefreshCcw, Save, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import {
  executeCandidateMigration,
  getSubscriptionAutomationSettings,
  previewCandidateMigration,
  updateSubscriptionAutomationSettings
} from '../../services/api';
import type {
  CandidateMigrationCategory,
  CandidateMigrationPreview,
  CandidateMigrationResult,
  SubscriptionAutomationSettings
} from '../../types/subscriptions';
import { createIdempotencyKey } from '../../utils/idempotency';
import type { PageId } from '../layout/AppTopNav';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import { SubscriptionHubSettings } from './SettingsPage';

interface SubscriptionSettingsPageProps {
  onNavigate: (page: PageId) => void;
}

function qualitySettingsError(settings: SubscriptionAutomationSettings, scheduleMinutes: number[]) {
  if (settings.lifecycleMode === 'fixed_window' && (scheduleMinutes.length === 0 || scheduleMinutes.some((value) => !Number.isInteger(value)))) {
    return '检查时间点必须填写整数分钟';
  }
  if (settings.lifecycleMode === 'fixed_window' && scheduleMinutes.some((value) => value < 30 || value > settings.defaultWindowHours * 60)) {
    return `检查时间点必须在 30 到 ${settings.defaultWindowHours * 60} 分钟之间`;
  }
  if (settings.lifecycleMode === 'fixed_window' && scheduleMinutes.some((value, index) => index > 0 && value <= scheduleMinutes[index - 1])) {
    return '检查时间点必须严格递增且不能重复';
  }
  if (!Number.isInteger(settings.minIntervalMinutes) || settings.minIntervalMinutes < 60 || settings.minIntervalMinutes > 1440) {
    return '最小间隔必须是 60 到 1440 分钟之间的整数';
  }
  if (!Number.isInteger(settings.hourlyLimit) || settings.hourlyLimit < 1 || settings.hourlyLimit > 1000) {
    return '每小时限额必须是 1 到 1000 之间的整数';
  }
  if (!Number.isInteger(settings.dailyLimit) || settings.dailyLimit < 1 || settings.dailyLimit > 1000) {
    return '每日限额必须是 1 到 1000 之间的整数';
  }
  if (!Number.isInteger(settings.batchSize) || settings.batchSize < 2 || settings.batchSize > 3) {
    return '每轮批量只能填写 2 或 3';
  }
  return '';
}

function QualityWatchSettings() {
  const [settings, setSettings] = useState<SubscriptionAutomationSettings | null>(null);
  const [scheduleText, setScheduleText] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getSubscriptionAutomationSettings({ signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setSettings(payload);
        setScheduleText(payload.scheduleMinutes.join(', '));
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setMessage(reason instanceof Error ? reason.message : '质量观察设置加载失败');
      });
    return () => controller.abort();
  }, []);

  if (!settings) return <div className="ops-settings-card ops-settings-card--wide ops-empty">{message || '质量观察设置加载中…'}</div>;

  const save = () => {
    const scheduleMinutes = scheduleText.split(/[\s,，]+/).filter(Boolean).map(Number);
    const validationError = qualitySettingsError(settings, scheduleMinutes);
    if (validationError) {
      setMessage(validationError);
      return;
    }
    setSaving(true);
    setMessage('');
    updateSubscriptionAutomationSettings({
      enabled: settings.enabled,
      missingFallbackEnabled: settings.missingFallbackEnabled,
      lifecycleMode: settings.lifecycleMode,
      defaultWindowHours: settings.defaultWindowHours,
      scheduleMinutes,
      minIntervalMinutes: settings.minIntervalMinutes,
      hourlyLimit: settings.hourlyLimit,
      dailyLimit: settings.dailyLimit,
      batchSize: settings.batchSize
    })
      .then((payload) => {
        setSettings(payload);
        setScheduleText(payload.scheduleMinutes.join(', '));
        setMessage('质量观察设置已保存');
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '质量观察设置保存失败'))
      .finally(() => setSaving(false));
  };

  return (
    <section className="ops-settings-card ops-settings-card--wide sub-config quality-settings">
      <header className="ops-settings-card__head">
        <div><span><SlidersHorizontal size={16} /></span><div><small>阶段 6 · 质量观察</small><h2>追更洗版策略</h2></div></div>
        <strong>{settings.environmentEnabled ? (settings.lifecycleMode === 'follow_rss' ? '跟随 RSS' : '固定窗口') : '服务端闸门未开启'}</strong>
      </header>
      <div className="sub-config__toggles">
        <label><input checked={settings.enabled} disabled={saving} type="checkbox" onChange={(event) => setSettings({ ...settings, enabled: event.target.checked })} />启用质量观察</label>
        <label><input checked={settings.missingFallbackEnabled} disabled={saving || !settings.enabled} type="checkbox" onChange={(event) => setSettings({ ...settings, missingFallbackEnabled: event.target.checked })} />缺集 PT 搜索兜底</label>
        <span className="quality-settings__readonly">下载闸门：{settings.downloadEnvironmentEnabled ? '已开启' : '未开启'}</span>
      </div>
      <div className="sub-config__row sub-config__row--pair">
        <label>观察模式<select disabled={saving} value={settings.lifecycleMode} onChange={(event) => setSettings({
          ...settings,
          lifecycleMode: event.target.value as SubscriptionAutomationSettings['lifecycleMode']
        })}><option value="follow_rss">跟随 RSS</option><option value="fixed_window">高级固定窗口</option></select></label>
        <label>{settings.lifecycleMode === 'follow_rss' ? '观察宽限期' : '固定观察窗口'}<select disabled={saving} value={settings.defaultWindowHours} onChange={(event) => {
          const defaultWindowHours = Number(event.target.value) as 24 | 48;
          setSettings({ ...settings, defaultWindowHours });
          setScheduleText(defaultWindowHours === 24 ? '720, 1440' : '720, 1440, 2880');
        }}><option value={24}>24 小时</option><option value={48}>48 小时</option></select></label>
      </div>
      {settings.lifecycleMode === 'fixed_window' && <>
        <div className="sub-config__row sub-config__row--pair">
          <label>检查时间点（分钟）<input disabled={saving} value={scheduleText} onChange={(event) => setScheduleText(event.target.value)} placeholder="720, 1440, 2880" /></label>
          <label>最小间隔（分钟）<input disabled={saving} min={60} max={1440} type="number" value={settings.minIntervalMinutes} onChange={(event) => setSettings({ ...settings, minIntervalMinutes: Number(event.target.value) })} /></label>
        </div>
        <div className="sub-config__row sub-config__row--pair">
          <label>每小时限额<input disabled={saving} min={1} max={1000} type="number" value={settings.hourlyLimit} onChange={(event) => setSettings({ ...settings, hourlyLimit: Number(event.target.value) })} /></label>
          <label>每日限额<input disabled={saving} min={1} max={1000} type="number" value={settings.dailyLimit} onChange={(event) => setSettings({ ...settings, dailyLimit: Number(event.target.value) })} /></label>
        </div>
        <div className="sub-config__row">
          <label>每轮批量<input disabled={saving} min={2} max={3} type="number" value={settings.batchSize} onChange={(event) => setSettings({ ...settings, batchSize: Number(event.target.value) })} /></label>
        </div>
      </>}
      <div className="sub-config__foot">
        <small>{settings.lifecycleMode === 'follow_rss' ? `候选即时评分 · 缺集兜底${settings.missingFallbackEnabled ? '已启用' : '未启用'}` : '高级兼容模式 · 按固定检查点分析'}</small>
        <button className="tool-link" disabled={saving} type="button" onClick={save}><Save size={14} />{saving ? '保存中…' : '保存质量观察设置'}</button>
        {message && <small role="status">{message}</small>}
      </div>
    </section>
  );
}

const migrationCategoryLabels: Record<CandidateMigrationCategory, string> = {
  manual: '人工追更',
  'downstream-owned': '已有下游',
  'candidate-eligible': '可迁候选',
  'migration-review': '待人工复核'
};

function CandidateMigrationSettings() {
  const [preview, setPreview] = useState<CandidateMigrationPreview | null>(null);
  const [result, setResult] = useState<CandidateMigrationResult | null>(null);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmPhrase, setConfirmPhrase] = useState('');

  const loadPreview = () => {
    setLoading(true);
    setMessage('');
    previewCandidateMigration()
      .then((payload) => {
        setPreview(payload);
        setResult(null);
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '历史追更预览失败'))
      .finally(() => setLoading(false));
  };

  const loadMore = () => {
    if (!preview?.page.hasMore || preview.page.nextOffset == null) return;
    setLoadingMore(true);
    setMessage('');
    previewCandidateMigration({ limit: preview.page.limit, offset: preview.page.nextOffset })
      .then((payload) => {
        if (payload.previewFingerprint !== preview.previewFingerprint) {
          setPreview(payload);
          setMessage('追更台账已变化，已重新读取最新预览。');
          return;
        }
        const items = new Map(preview.items.map((item) => [item.id, item]));
        payload.items.forEach((item) => items.set(item.id, item));
        setPreview({ ...payload, items: Array.from(items.values()) });
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '更多分类记录读取失败'))
      .finally(() => setLoadingMore(false));
  };

  const openConfirmation = () => {
    if (!preview?.canExecute || preview.counts['candidate-eligible'] < 1) return;
    setConfirmPhrase('');
    setConfirmOpen(true);
  };

  const execute = () => {
    if (!preview || confirmPhrase !== '迁移候选追更') return;
    setExecuting(true);
    setMessage('');
    executeCandidateMigration({
      confirm: true,
      idempotencyKey: createIdempotencyKey(),
      previewFingerprint: preview.previewFingerprint
    })
      .then((payload) => {
        setResult(payload);
        setConfirmOpen(false);
        setMessage(`已迁入候选池 ${payload.migratedCount} 条，保留 ${payload.preservedCount} 条。`);
        void previewCandidateMigration()
          .then(setPreview)
          .catch(() => setMessage(`已迁入候选池 ${payload.migratedCount} 条；请重新预览当前台账。`));
      })
      .catch((reason: unknown) => {
        setConfirmOpen(false);
        setMessage(reason instanceof Error ? reason.message : '历史追更迁移失败');
      })
      .finally(() => setExecuting(false));
  };

  return (
    <section className="ops-settings-card ops-settings-card--wide sub-config candidate-migration">
      <header className="ops-settings-card__head">
        <div><span><Database size={16} /></span><div><small>P0.5 · 台账整理</small><h2>历史追更分类</h2></div></div>
        <strong>{preview ? `${preview.total} 条已分类` : '尚未预览'}</strong>
      </header>

      {!preview && !loading && (
        <div className="candidate-migration__empty">
          <span>先读取当前追更证据，再决定是否迁移自动来源记录。</span>
        </div>
      )}

      {preview && (
        <>
          <div className="candidate-migration__summary" aria-label="历史追更分类统计">
            {(Object.keys(migrationCategoryLabels) as CandidateMigrationCategory[]).map((category) => (
              <div key={category}>
                <strong>{preview.counts[category]}</strong>
                <span>{migrationCategoryLabels[category]}</span>
              </div>
            ))}
          </div>
          <div className="candidate-migration__groups">
            {(Object.keys(migrationCategoryLabels) as CandidateMigrationCategory[]).map((category) => {
              const items = preview.items.filter((item) => item.category === category);
              return (
                <details key={category} open={category === 'candidate-eligible' && items.length > 0}>
                  <summary><span>{migrationCategoryLabels[category]}</span><strong>{items.length}</strong></summary>
                  {items.length > 0 ? (
                    <ul>
                      {items.map((item) => (
                        <li key={item.id}>
                          <div><strong>{item.title || '未命名作品'}</strong><small>{item.mediaType === 'tv' ? `电视剧 · 第 ${item.seasonNumber || '?'} 季` : item.mediaType === 'movie' ? '电影' : '类型未确认'}{item.tmdbId ? ` · TMDB ${item.tmdbId}` : ''}</small></div>
                          <span>{item.reasonText}</span>
                        </li>
                      ))}
                    </ul>
                  ) : <p>当前没有此类记录。</p>}
                </details>
              );
            })}
          </div>
        </>
      )}

      <div className="sub-config__foot">
        <button className="tool-link" disabled={loading || executing} type="button" onClick={loadPreview}>
          <RefreshCcw aria-hidden="true" size={14} />
          {loading ? '读取中…' : preview ? '重新预览' : '预览现有追更'}
        </button>
        {preview?.page.hasMore && (
          <button className="tool-link" disabled={loadingMore || loading || executing} type="button" onClick={loadMore}>
            {loadingMore ? '读取中…' : `加载更多（${preview.items.length}/${preview.page.total}）`}
          </button>
        )}
        {preview && preview.counts['candidate-eligible'] > 0 && (
          <button className="ops-action-button ops-action-button--primary" disabled={!preview.canExecute || loading || executing} type="button" onClick={openConfirmation}>
            迁入候选池 {preview.counts['candidate-eligible']} 条
          </button>
        )}
        {message && <small role="status">{message}</small>}
        {result && <small>备份：{result.backupId}</small>}
      </div>

      <ConfirmDialog
        busy={executing}
        labelledBy="candidate-migration-title"
        describedBy="candidate-migration-description"
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
      >
        <span className="ops-confirm-dialog__signal ops-confirm-dialog__signal--danger">台账迁移</span>
        <h2 id="candidate-migration-title">迁移自动来源追更？</h2>
        <p id="candidate-migration-description">将 {preview?.counts['candidate-eligible'] ?? 0} 条自动来源记录移入候选池。人工追更、已有下游证据和待复核记录会保留；执行前创建 SQLite 备份。</p>
        <label className="ops-confirm-dialog__input">
          输入“迁移候选追更”
          <input
            autoComplete="off"
            data-dialog-initial-focus
            value={confirmPhrase}
            onChange={(event) => setConfirmPhrase(event.target.value)}
          />
        </label>
        <div className="ops-confirm-dialog__actions">
          <button className="ops-action-button" disabled={executing} type="button" onClick={() => setConfirmOpen(false)}>取消</button>
          <button className="ops-action-button ops-action-button--danger" disabled={executing || confirmPhrase !== '迁移候选追更'} type="button" onClick={execute}>{executing ? '迁移中…' : '确认迁移'}</button>
        </div>
      </ConfirmDialog>
    </section>
  );
}

export function SubscriptionSettingsPage({ onNavigate }: SubscriptionSettingsPageProps) {
  const [modeLabel, setModeLabel] = useState('读取中');

  return (
    <main className="work-page ops-page ops-page--subscription-settings">
      <section className="ops-hero ops-hero--subscription-settings">
        <div>
          <button className="ops-back-link" type="button" onClick={() => onNavigate('subscriptions')}>
            <ArrowLeft aria-hidden="true" size={14} />
            返回我的订阅
          </button>
          <p className="ops-eyebrow">来源与时间</p>
          <h1>订阅设置</h1>
          <p className="ops-page-subtitle">设置系统从哪些来源更新发现候选。</p>
          <p className="ops-deck">候选刷新不会建立追更；只有你在发现页确认加入后，内容才进入 PT 主线。</p>
        </div>
        <div className="ops-subscription-settings-guard">
          <span><Database size={15} />当前 PT 通道</span>
          <strong>{modeLabel}</strong>
          <small><ShieldCheck size={13} />真实外部写入仍受安全开关控制</small>
        </div>
      </section>

      <section className="ops-settings-grid ops-settings-grid--subscription">
        <SubscriptionHubSettings onModeChange={setModeLabel} />
        <CandidateMigrationSettings />
        <QualityWatchSettings />
      </section>
    </main>
  );
}
