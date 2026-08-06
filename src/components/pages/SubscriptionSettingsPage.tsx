import { useEffect, useState } from 'react';
import { ArrowLeft, CheckCircle2, Database, History, RefreshCcw, Save, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import {
  executeBaselineInitialization,
  executeCandidateMigration,
  getQualityWatchBridgeSummary,
  getSubscriptionAutomationSettings,
  previewBaselineInitialization,
  previewCandidateMigration,
  updateSubscriptionAutomationSettings
} from '../../services/api';
import type {
  BaselineInitializationCategory,
  BaselineInitializationPreview,
  BaselineInitializationResult,
  CandidateMigrationCategory,
  CandidateMigrationPreview,
  CandidateMigrationResult,
  QualityWatchBridgeMode,
  QualityWatchBridgeSummary,
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

const bridgeModeLabels: Record<QualityWatchBridgeMode, string> = {
  off: '关闭',
  shadow: '影子',
  apply: '正式'
};

const analysisStateLabels: Record<string, string> = {
  disabled: '自动评分未开启',
  collecting: '正在收集候选',
  scoring: 'Torra 规则评分中',
  ready: 'Torra 规则评分正常',
  blocked: '评分存在阻断',
  unknown: '评分状态暂未确认'
};

const executionModeLabels: Record<string, string> = {
  disabled: '未授权',
  manual: '仅人工确认',
  automatic: '自动执行'
};

const baselineCategoryLabels: Record<BaselineInitializationCategory, string> = {
  safe_to_initialize: '可安全初始化',
  needs_review: '需要复核',
  skipped: '已跳过'
};

const baselineReasonLabels: Record<string, string> = {
  eligible: '证据完整',
  identity_incomplete: '身份信息不完整',
  identity_conflict: '身份信息冲突',
  observation_unit_exists: '已有观察单元',
  artifact_owner_unconfirmed: '文件所有权未确认',
  success_file_missing: '缺少成功文件证据',
  success_file_conflict: '成功文件证据冲突',
  success_time_missing: '缺少正式发生时间',
  success_time_inverted: '历史时间顺序异常',
  policy_invalid: '当前策略无效'
};

function localTime(value: string) {
  if (!value) return '尚未建立';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '时间暂未确认';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(parsed);
}

function QualityWatchBridgeSettings() {
  const [summary, setSummary] = useState<QualityWatchBridgeSummary | null>(null);
  const [preview, setPreview] = useState<BaselineInitializationPreview | null>(null);
  const [result, setResult] = useState<BaselineInitializationResult | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);
  const [pendingMode, setPendingMode] = useState<QualityWatchBridgeMode | null>(null);
  const [modeConfirmOpen, setModeConfirmOpen] = useState(false);
  const [initConfirmOpen, setInitConfirmOpen] = useState(false);

  const refreshSummary = () => getQualityWatchBridgeSummary().then(setSummary);

  useEffect(() => {
    const controller = new AbortController();
    getQualityWatchBridgeSummary({ signal: controller.signal })
      .then((payload) => {
        if (!controller.signal.aborted) setSummary(payload);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setMessage(reason instanceof Error ? reason.message : '生产桥接状态加载失败');
      });
    return () => controller.abort();
  }, []);

  const applyMode = (mode: QualityWatchBridgeMode) => {
    setModeBusy(true);
    setMessage('');
    updateSubscriptionAutomationSettings({ bridgeMode: mode, bridgeModeConfirm: true })
      .then(() => refreshSummary())
      .then(() => {
        setModeConfirmOpen(false);
        setPendingMode(null);
        setMessage(`生产桥接已切换为${bridgeModeLabels[mode]}模式`);
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '生产桥接模式切换失败'))
      .finally(() => setModeBusy(false));
  };

  const requestMode = (mode: QualityWatchBridgeMode) => {
    if (!summary || modeBusy || mode === summary.mode) return;
    if (mode === 'apply') {
      setPendingMode(mode);
      setModeConfirmOpen(true);
      return;
    }
    applyMode(mode);
  };

  const loadPreview = () => {
    setLoading(true);
    setMessage('');
    previewBaselineInitialization()
      .then((payload) => {
        setPreview(payload);
        setResult(null);
        const safeIds = payload.groups
          .flatMap((group) => group.items)
          .filter((item) => item.category === 'safe_to_initialize')
          .slice(0, payload.maxSelectedTargets)
          .map((item) => item.id);
        setSelected(safeIds);
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '历史基线预览失败'))
      .finally(() => setLoading(false));
  };

  const toggleSelected = (id: string) => {
    if (!preview) return;
    setSelected((current) => {
      if (current.includes(id)) return current.filter((value) => value !== id);
      if (current.length >= preview.maxSelectedTargets) return current;
      return [...current, id];
    });
  };

  const execute = () => {
    if (!preview || selected.length === 0) return;
    setLoading(true);
    setMessage('');
    executeBaselineInitialization({
      confirm: true,
      runId: preview.runId,
      previewFingerprint: preview.previewFingerprint,
      selectedTargetIds: selected,
      idempotencyKey: createIdempotencyKey()
    })
      .then((payload) => {
        setResult(payload);
        setInitConfirmOpen(false);
        setMessage(`已处理 ${payload.processed} 集 · 进入观察 ${payload.initialized} · 历史过期 ${payload.expired}`);
      })
      .catch((reason: unknown) => {
        setInitConfirmOpen(false);
        setMessage(reason instanceof Error ? reason.message : '历史基线初始化失败');
      })
      .finally(() => setLoading(false));
  };

  const receiptCounts = summary?.receiptCounts;

  return (
    <div className="quality-bridge">
      <div className="quality-bridge__heading">
        <div><ShieldCheck size={15} /><span><strong>生产桥接</strong><small>永久水位 {summary ? localTime(summary.activatedAt) : '读取中'}</small></span></div>
        <span>{summary ? `${summary.receiptTotal} 条收据` : '状态读取中'}</span>
      </div>
      <div className="quality-bridge__modes" role="group" aria-label="生产桥接模式">
        {(Object.keys(bridgeModeLabels) as QualityWatchBridgeMode[]).map((mode) => (
          <button
            aria-pressed={summary?.mode === mode}
            className={summary?.mode === mode ? 'is-active' : ''}
            disabled={!summary || modeBusy}
            key={mode}
            onClick={() => requestMode(mode)}
            type="button"
          >
            <strong>{bridgeModeLabels[mode]}</strong>
            <small>{mode === 'off' ? '不接收新事实' : mode === 'shadow' ? '只判定并记收据' : '应用新事实'}</small>
          </button>
        ))}
      </div>
      {summary && (
        <div className="quality-bridge__receipts" aria-label="生产桥接收据统计">
          <span>待应用 <strong>{receiptCounts?.pending ?? 0}</strong></span>
          <span>已应用 <strong>{receiptCounts?.applied ?? 0}</strong></span>
          <span>历史 <strong>{receiptCounts?.historical ?? 0}</strong></span>
          <span>待复核 <strong>{receiptCounts?.needs_review ?? 0}</strong></span>
          <span>可重试失败 <strong>{receiptCounts?.retryable_failed ?? 0}</strong></span>
        </div>
      )}

      <div className="baseline-init__heading">
        <div><History size={15} /><span><strong>历史基线初始化</strong><small>{preview ? `预览于 ${localTime(preview.generatedAt)}` : '尚未预览'}</small></span></div>
        <button className="tool-link" disabled={loading || modeBusy} type="button" onClick={loadPreview}>
          <RefreshCcw size={14} />{loading ? '读取中…' : preview ? '重新预览' : '创建预览'}
        </button>
      </div>

      {preview && (
        <>
          <div className="baseline-init__summary">
            <span><strong>{preview.counts.safeToInitialize}</strong> 可安全初始化</span>
            <span><strong>{preview.counts.needsReview}</strong> 需要复核</span>
            <span><strong>{preview.counts.skipped}</strong> 已跳过</span>
          </div>
          <div className="baseline-init__groups">
            {preview.groups.map((group) => (
              <details key={`${group.id}:${group.seasonNumber}`}>
                <summary><span>{group.subscriptionTitle || '未命名订阅'} · {group.seasonNumber > 0 ? `第 ${group.seasonNumber} 季` : '季数暂未确认'}</span><strong>{group.items.length} 条</strong></summary>
                <ul>
                  {group.items.map((item) => (
                    <li key={item.id}>
                      <label>
                        <input
                          checked={selected.includes(item.id)}
                          disabled={item.category !== 'safe_to_initialize' || loading}
                          onChange={() => toggleSelected(item.id)}
                          type="checkbox"
                        />
                        <span><strong>{item.episodeNumber > 0 ? `E${String(item.episodeNumber).padStart(2, '0')}` : '集号暂未确认'}</strong><small>{baselineCategoryLabels[item.category]} · {baselineReasonLabels[item.reasonCode] || item.reasonCode}</small></span>
                      </label>
                      <span>{item.evidenceSource === 'symedia' ? 'Symedia 归档' : item.evidenceSource === 'qb' ? 'qB 完成' : 'Torra 完成'} · {localTime(item.baselineReadyAt)}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
          <div className="baseline-init__actions">
            <small>已选择 {selected.length}/{preview.maxSelectedTargets} 集</small>
            <button className="ops-action-button ops-action-button--primary" disabled={selected.length === 0 || loading} onClick={() => setInitConfirmOpen(true)} type="button">
              <CheckCircle2 size={14} />初始化所选基线
            </button>
          </div>
        </>
      )}
      {result && <div className="baseline-init__result">已处理 {result.processed} 集 · 进入观察 {result.initialized} · 历史过期 {result.expired}</div>}
      {message && <small className="quality-bridge__message" role="status">{message}</small>}

      <ConfirmDialog
        busy={modeBusy}
        labelledBy="quality-bridge-confirm-title"
        describedBy="quality-bridge-confirm-description"
        open={modeConfirmOpen}
        onClose={() => !modeBusy && setModeConfirmOpen(false)}
      >
        <span className="ops-confirm-dialog__signal">生产桥接</span>
        <h2 id="quality-bridge-confirm-title">启用正式桥接？</h2>
        <p id="quality-bridge-confirm-description">后续新完成事实会按同一事务写入质量观察；不会触发搜索、下载或秒传。</p>
        <div className="ops-confirm-dialog__actions">
          <button className="ops-action-button" disabled={modeBusy} onClick={() => setModeConfirmOpen(false)} type="button">取消</button>
          <button className="ops-action-button ops-action-button--primary" disabled={modeBusy} onClick={() => pendingMode && applyMode(pendingMode)} type="button">{modeBusy ? '切换中…' : '确认启用'}</button>
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        busy={loading}
        labelledBy="baseline-init-confirm-title"
        describedBy="baseline-init-confirm-description"
        open={initConfirmOpen}
        onClose={() => !loading && setInitConfirmOpen(false)}
      >
        <span className="ops-confirm-dialog__signal">历史基线</span>
        <h2 id="baseline-init-confirm-title">初始化 {selected.length} 集历史基线？</h2>
        <p id="baseline-init-confirm-description">将按预览中的真实历史时间写入观察单元。已过观察期限的记录只保留历史状态。</p>
        <div className="ops-confirm-dialog__actions">
          <button className="ops-action-button" disabled={loading} onClick={() => setInitConfirmOpen(false)} type="button">取消</button>
          <button className="ops-action-button ops-action-button--primary" disabled={loading} onClick={execute} type="button">{loading ? '执行中…' : '确认初始化'}</button>
        </div>
      </ConfirmDialog>
    </div>
  );
}

function QualityWatchSettings() {
  const [settings, setSettings] = useState<SubscriptionAutomationSettings | null>(null);
  const [scheduleText, setScheduleText] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);
  const [pendingExecutionMode, setPendingExecutionMode] = useState<'disabled' | 'manual' | null>(null);

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

  const applyExecutionMode = () => {
    if (!pendingExecutionMode) return;
    setModeBusy(true);
    setMessage('');
    updateSubscriptionAutomationSettings({
      executionMode: pendingExecutionMode,
      executionModeConfirm: true
    })
      .then((payload) => {
        setSettings(payload);
        setPendingExecutionMode(null);
        setMessage(payload.executionMode === 'manual' ? '已启用仅人工确认' : '已关闭精准下载执行授权');
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '执行模式切换失败'))
      .finally(() => setModeBusy(false));
  };

  return (
    <section className="ops-settings-card ops-settings-card--wide sub-config quality-settings">
      <header className="ops-settings-card__head">
        <div><span><SlidersHorizontal size={16} /></span><div><small>阶段 6 · 质量观察</small><h2>追更洗版策略</h2></div></div>
        <strong>{analysisStateLabels[settings.analysisState ?? ''] || (settings.environmentEnabled ? '评分状态暂未确认' : '服务端闸门未开启')}</strong>
      </header>
      <div className="sub-config__toggles">
        <label><input checked={settings.enabled} disabled={saving} type="checkbox" onChange={(event) => setSettings({ ...settings, enabled: event.target.checked })} />自动评分</label>
        <label><input checked={settings.missingFallbackEnabled} disabled={saving || !settings.enabled} type="checkbox" onChange={(event) => setSettings({ ...settings, missingFallbackEnabled: event.target.checked })} />缺集 PT 搜索兜底</label>
        <span className="quality-settings__readonly">执行模式：{executionModeLabels[settings.executionMode ?? 'disabled'] || '暂未确认'} · 硬门禁：{(settings.executionEnvironmentEnabled ?? settings.downloadEnvironmentEnabled) ? '已开启' : '未开启'}</span>
      </div>
      <div className="quality-bridge__modes" role="group" aria-label="精准下载执行模式">
        {(['disabled', 'manual'] as const).map((mode) => (
          <button
            aria-pressed={(settings.executionMode ?? 'disabled') === mode}
            className={(settings.executionMode ?? 'disabled') === mode ? 'is-active' : ''}
            disabled={saving || modeBusy}
            key={mode}
            onClick={() => (settings.executionMode ?? 'disabled') !== mode && setPendingExecutionMode(mode)}
            type="button"
          >
            <strong>{mode === 'disabled' ? '不允许执行' : '仅人工确认'}</strong>
            <small>{mode === 'disabled' ? '继续评分，不提交下载' : '每次预检后明确确认'}</small>
          </button>
        ))}
      </div>
      {settings.baselineCounts && (
        <div className="quality-bridge__receipts" aria-label="当前版本基线统计">
          <span>基线已确认 <strong>{settings.baselineCounts.ready}</strong></span>
          <span>等待基线 <strong>{settings.baselineCounts.pending}</strong></span>
          <span>缺少基线 <strong>{settings.baselineCounts.missing}</strong></span>
          <span>基线冲突 <strong>{settings.baselineCounts.conflict}</strong></span>
          <span>历史过期 <strong>{settings.baselineCounts.expired}</strong></span>
          <span>可执行冠军 <strong>{settings.automaticEligibleCount ?? '暂未确认'}</strong></span>
        </div>
      )}
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
      <QualityWatchBridgeSettings />
      <div className="sub-config__foot">
        <small>{settings.lifecycleMode === 'follow_rss' ? `候选即时评分 · 缺集兜底${settings.missingFallbackEnabled ? '已启用' : '未启用'}` : '高级兼容模式 · 按固定检查点分析'}</small>
        <button className="tool-link" disabled={saving} type="button" onClick={save}><Save size={14} />{saving ? '保存中…' : '保存质量观察设置'}</button>
        {message && <small role="status">{message}</small>}
      </div>
      <ConfirmDialog
        busy={modeBusy}
        labelledBy="quality-execution-mode-title"
        describedBy="quality-execution-mode-description"
        open={Boolean(pendingExecutionMode)}
        onClose={() => !modeBusy && setPendingExecutionMode(null)}
      >
        <span className="ops-confirm-dialog__signal">精准下载执行授权</span>
        <h2 id="quality-execution-mode-title">{pendingExecutionMode === 'manual' ? '启用仅人工确认？' : '关闭精准下载执行？'}</h2>
        <p id="quality-execution-mode-description">{pendingExecutionMode === 'manual' ? '启用后仍需对每个唯一冠军完成预检并明确确认；自动执行不会开启。' : '关闭后继续收集和评分 RSS 候选，但不会再提交新的精准下载。'}</p>
        <div className="ops-confirm-dialog__actions">
          <button className="ops-action-button" disabled={modeBusy} onClick={() => setPendingExecutionMode(null)} type="button">取消</button>
          <button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus disabled={modeBusy} onClick={applyExecutionMode} type="button">{modeBusy ? '切换中…' : '确认切换'}</button>
        </div>
      </ConfirmDialog>
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
