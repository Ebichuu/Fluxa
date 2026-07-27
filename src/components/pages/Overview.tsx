import { useState } from 'react';
import {
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  Download,
  Library,
  RefreshCw,
  ShieldCheck,
  TriangleAlert
} from 'lucide-react';
import { usePolling } from '../../hooks/usePolling';
import { getHomeSummary } from '../../services/api';
import type { HealthState, HomeSummaryFocusItem, HomeSummaryResponse } from '../../types/homeSummary';
import { readLocalStorage, writeLocalStorage } from '../../utils/storage';
import type { AppNavigate, AppPathNavigate } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';
import { RelativeTime } from '../status/RelativeTime';

interface OverviewProps {
  onNavigate: AppNavigate;
  onNavigatePath: AppPathNavigate;
}

const metricDefinitions = [
  { key: 'archivedToday', label: '归档文件', unit: '个文件', icon: Library, target: null },
  { key: 'playableToday', label: '已可播放', unit: '个', icon: ShieldCheck, target: 'playable' },
  { key: 'activeDownloadTasks', label: 'qB 下载任务', unit: '个', icon: Download, target: 'in_progress' },
  { key: 'mediaActionRequired', label: '媒体需处理', unit: '项', icon: TriangleAlert, target: 'action_required' }
] as const;

function shanghaiDateKey(value = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function emptyFocusItem(key: HomeSummaryFocusItem['key'], label: string, unit: string, href: string): HomeSummaryFocusItem {
  return { key, label, unit, href, value: null, state: 'unknown', detail: '正在读取证据' };
}

function emptySummary(): HomeSummaryResponse {
  return {
    ok: false,
    generatedAt: '',
    healthState: 'evidence_insufficient',
    headline: '正在读取影音中心状态',
    detail: '正在汇总下载、入库和调度证据',
    counts: { ingestedToday: 0, archivedToday: null, completedTargetsToday: 0, playableToday: 0, downloading: 0, activeDownloadTasks: null, concurrentDownloadGroups: 0, pending: 0, waiting: 0, evidenceInsufficient: 0, identityPending: 0, actionRequired: 0, mediaActionRequired: 0, auxiliaryAlerts: 0, inProgress: 0, suspectedBlocked: 0, protected: 0 },
    focusItems: [
      emptyFocusItem('current_downloads', '当前下载', '个', '/tasks?outcomeState=in_progress'),
      emptyFocusItem('secupload_failures', '秒传失败', '个', '/tasks?systemIssue=secupload_failures'),
      emptyFocusItem('downloaded_not_archived', '下载完成未入库', '个', '/tasks?outcomeState=in_progress'),
      emptyFocusItem('archived_today', '今日入库', '个文件', `/tasks?outcomeState=playable&completedDate=${shanghaiDateKey()}`),
      emptyFocusItem('missing_episodes', '追更缺集', '集', '/following?missingEpisodes=1'),
      emptyFocusItem('action_required', '真实异常', '项', '/tasks?outcomeState=action_required')
    ],
    issueTotal: 0,
    issues: [],
    diagnostics: [],
    diagnosticTotal: 0
  };
}

export function Overview({ onNavigate, onNavigatePath }: OverviewProps) {
  const [summary, setSummary] = useState<HomeSummaryResponse>(emptySummary);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [showAllIssues, setShowAllIssues] = useState(false);
  const [showWelcome, setShowWelcome] = useState(() => readLocalStorage('fluxa:welcome-dismissed') !== '1');

  const loadSummary = async (signal: AbortSignal) => {
    setRefreshing(true);
    try {
      const value = await getHomeSummary({ signal });
      if (!signal.aborted) {
        setSummary(value);
        setError('');
      }
    } catch (reason) {
      if (!signal.aborted) {
        setError(reason instanceof Error ? reason.message : '首页状态读取失败');
      }
    } finally {
      if (!signal.aborted) setRefreshing(false);
    }
  };

  usePolling(loadSummary, 15_000);

  const status = error ? 'evidence_insufficient' : summary.healthState;
  const issues = showAllIssues ? summary.issues : summary.issues.slice(0, 4);
  const diagnostics = summary.diagnostics ?? [];
  const loadedHiddenIssueCount = Math.max(0, summary.issues.length - 4);
  const unloadedIssueCount = Math.max(0, (summary.issueTotal ?? summary.issues.length) - summary.issues.length);
  const StatusIcon = status === 'normal' ? CheckCircle2 : status === 'action_required' ? TriangleAlert : Clock3;

  const openMetric = (target: typeof metricDefinitions[number]['target']) => {
    if (target === 'playable') {
      onNavigate('tasks', { outcomeState: 'playable', completedDate: shanghaiDateKey() });
      return;
    }
    onNavigate('tasks', target ? { outcomeState: target } : undefined);
  };

  const openSource = (source?: string) => {
    if (source === 'private-rss') {
      onNavigate('rss-library');
      return;
    }
    onNavigate(source === 'subscription-scheduler' ? 'subscriptions' : 'control');
  };

  const openIssue = (issue: HomeSummaryResponse['issues'][number]) => {
    if (issue.href) {
      onNavigatePath(issue.href);
      return;
    }
    if (!issue.chainId && !issue.targetKey) {
      openSource(issue.source);
      return;
    }
    onNavigate('tasks', {
      chainId: issue.chainId || undefined,
      targetKey: issue.targetKey || undefined,
      title: issue.title,
      outcomeState: 'action_required'
    });
  };

  const openDiagnostic = (diagnostic: NonNullable<HomeSummaryResponse['diagnostics']>[number]) => {
    if (diagnostic.code === 'TASK_IDENTITY_PENDING') {
      onNavigate('tasks', { advanced: true, identityStates: ['unidentified', 'conflict'] });
      return;
    }
    openSource(diagnostic.source);
  };

  const dismissWelcome = () => {
    writeLocalStorage('fluxa:welcome-dismissed', '1');
    setShowWelcome(false);
  };

  return (
    <main className={`work-page ops-page ops-page--overview home-summary home-summary--${status}`}>
      <section className="home-summary__hero" aria-live="polite">
        <div className="home-summary__headline">
          <p className="ops-eyebrow">首页 · 今日状态</p>
          <span className="home-summary__status-icon" aria-hidden="true"><StatusIcon size={22} /></span>
          <h1>{error ? '暂时无法确认影音中心状态' : summary.headline}</h1>
          <p>{error || summary.detail}</p>
          <small>{summary.generatedAt ? <>最近读取 <RelativeTime value={summary.generatedAt} /></> : '等待第一份状态证据'}</small>
        </div>
        <div className="home-summary__hero-actions">
          <button
            className="home-icon-button"
            type="button"
            onClick={() => void loadSummary(new AbortController().signal)}
            disabled={refreshing}
            aria-label="刷新今日状态"
            title="刷新今日状态"
          >
            <RefreshCw aria-hidden="true" className={refreshing ? 'is-spinning' : ''} size={18} />
          </button>
          <button className="home-primary-action" type="button" onClick={() => onNavigate('calendar')}>
            <CalendarDays aria-hidden="true" size={16} />今日更新
          </button>
          <button className="home-primary-action" type="button" onClick={() => onNavigate('tasks')}>
            <span className="home-primary-action__full">查看任务中心</span>
            <span className="home-primary-action__short">任务中心</span>
            <ArrowRight aria-hidden="true" size={16} />
          </button>
        </div>
      </section>

      {showWelcome && (
        <section className="home-welcome" aria-label="Fluxa 使用说明">
          <div>
            <strong>第一次使用 Fluxa</strong>
            <p>Fluxa 帮你追更并查看每部作品的处理进度。</p>
            <p>资源会经过下载、网盘和入库，最后出现在 Emby。</p>
            <p>平时只处理首页红色项目；蓝色正在执行，灰色可以等待。</p>
          </div>
          <button className="tool-link" type="button" onClick={dismissWelcome}>知道了</button>
        </section>
      )}

      <section className="home-metrics" aria-label="今日媒体处理统计">
        {metricDefinitions.map(({ key, label, unit, icon: Icon, target }) => {
          const value = summary.counts[key];
          return (
            <button className={`home-metric home-metric--${key}`} key={key} type="button" onClick={() => openMetric(target)}>
              <span aria-hidden="true"><Icon size={17} /></span>
              <small>{label}</small>
              <strong><b>{value ?? '—'}</b><em>{value == null ? '未知' : unit}</em></strong>
            </button>
          );
        })}
      </section>

      <section className="home-focus" aria-labelledby="home-focus-title">
        <header className="home-section-heading">
          <div>
            <p className="ops-eyebrow">固定入口</p>
            <h2 id="home-focus-title">我的关注</h2>
          </div>
          <small>只展示已有证据</small>
        </header>
        <div className="home-focus__list">
          {summary.focusItems.map((item) => (
            <a
              className={`home-focus__item home-focus__item--${item.state}`}
              href={item.href}
              key={item.key}
              onClick={(event) => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                onNavigatePath(item.href);
              }}
            >
              <span className="home-focus__marker" aria-hidden="true" />
              <span className="home-focus__copy">
                <strong>{item.label}</strong>
                <small>{item.detail}</small>
              </span>
              <span className="home-focus__value">
                <b>{item.value ?? '—'}</b>
                <em>{item.value == null ? '未知' : item.unit}</em>
              </span>
              <ArrowRight aria-hidden="true" size={15} />
            </a>
          ))}
        </div>
      </section>

      <section className="home-issues" aria-labelledby="home-issues-title">
        <header className="home-section-heading">
          <div>
            <p className="ops-eyebrow">下一步</p>
            <h2 id="home-issues-title">{issues.length > 0 ? '需要关注' : '当前没有明确异常'}</h2>
          </div>
          <HealthBadge label={status === 'normal' ? '运行正常' : status === 'waiting' ? '正在处理' : undefined} state={status} />
        </header>

        {issues.length === 0 ? (
          <button className="home-clear-state" type="button" onClick={() => onNavigate('tasks')}>
            <CheckCircle2 aria-hidden="true" size={20} />
            <span>
              <strong>{status === 'normal' ? '今天的媒体处理没有发现需要介入的问题' : '当前没有可定位的问题记录'}</strong>
              <small>{status === 'normal' ? '可以继续使用，任务中心保留完整处理证据。' : '可进入任务中心查看各阶段证据。'}</small>
            </span>
            <ArrowRight aria-hidden="true" size={16} />
          </button>
        ) : (
          <div className="home-issue-list">
            {issues.map((issue, index) => (
              <button
                className={`home-issue home-issue--${issue.healthState}`}
                type="button"
                key={`${issue.source}:${issue.reasonCode}:${issue.chainId || index}`}
                onClick={() => openIssue(issue)}
              >
                <span className="home-issue__marker" aria-hidden="true" />
                <span className="home-issue__copy">
                  <strong>{issue.headline || issue.displayTitle || issue.title}</strong>
                  <small>
                    {issue.reasonText || '查看任务详情'}
                    {issue.secondaryReasonText && ` · ${issue.secondaryReasonText}`}
                    {' · '}<RelativeTime interactive={false} value={issue.observedAt} />
                  </small>
                </span>
                <HealthBadge state={issue.healthState} />
                <ArrowRight aria-hidden="true" size={16} />
              </button>
            ))}
            {loadedHiddenIssueCount > 0 && (
              <button
                aria-expanded={showAllIssues}
                className="home-issues-more"
                type="button"
                onClick={() => setShowAllIssues((current) => !current)}
              >
                {showAllIssues ? '收起其余问题' : `展开另外 ${loadedHiddenIssueCount} 项`}
                {showAllIssues
                  ? <ChevronUp aria-hidden="true" size={15} />
                  : <ChevronDown aria-hidden="true" size={15} />}
              </button>
            )}
            {showAllIssues && unloadedIssueCount > 0 && (
              <small className="home-issues-unloaded" aria-live="polite">
                另有 {unloadedIssueCount} 项未在首页加载
              </small>
            )}
          </div>
        )}
      </section>

      {diagnostics.length > 0 && (
        <details className="home-diagnostics">
          <summary>高级诊断 · {summary.diagnosticTotal ?? diagnostics.length} 项数据整理提醒</summary>
          <div>
            {diagnostics.map((diagnostic) => (
              <button
                key={`${diagnostic.code}:${diagnostic.source || ''}`}
                type="button"
                onClick={() => openDiagnostic(diagnostic)}
              >
                <span><strong>{diagnostic.label}</strong><small>{diagnostic.reasonText}</small></span>
                <ArrowRight aria-hidden="true" size={15} />
              </button>
            ))}
          </div>
        </details>
      )}
    </main>
  );
}
