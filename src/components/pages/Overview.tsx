import { useState } from 'react';
import {
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  CircleHelp,
  Clock3,
  Download,
  Library,
  RefreshCw,
  ShieldCheck,
  TriangleAlert
} from 'lucide-react';
import { usePolling } from '../../hooks/usePolling';
import { getHomeSummary } from '../../services/api';
import type { HealthState, HomeSummaryResponse } from '../../types/homeSummary';
import { formatTimeAgo } from '../../utils/formatters';
import type { AppNavigate } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';

interface OverviewProps {
  onNavigate: AppNavigate;
}

const metricDefinitions = [
  { key: 'ingestedToday', label: '今日入库', icon: Library },
  { key: 'downloading', label: '下载中', icon: Download },
  { key: 'waiting', label: '等待', icon: Clock3 },
  { key: 'suspectedBlocked', label: '疑似阻塞', icon: TriangleAlert },
  { key: 'evidenceInsufficient', label: '证据不足', icon: CircleHelp },
  { key: 'actionRequired', label: '需要处理', icon: TriangleAlert },
  { key: 'protected', label: '正常保护', icon: ShieldCheck }
] as const;

function emptySummary(): HomeSummaryResponse {
  return {
    ok: false,
    generatedAt: '',
    healthState: 'evidence_insufficient',
    headline: '正在读取影音中心状态',
    detail: '正在汇总下载、入库和调度证据',
    counts: { ingestedToday: 0, downloading: 0, pending: 0, waiting: 0, evidenceInsufficient: 0, actionRequired: 0, suspectedBlocked: 0, protected: 0 },
    issues: []
  };
}

export function Overview({ onNavigate }: OverviewProps) {
  const [summary, setSummary] = useState<HomeSummaryResponse>(emptySummary);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

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
  const issues = summary.issues.slice(0, 4);
  const StatusIcon = status === 'normal' ? CheckCircle2 : status === 'action_required' ? TriangleAlert : Clock3;

  return (
    <main className={`work-page ops-page ops-page--overview home-summary home-summary--${status}`}>
      <section className="home-summary__hero" aria-live="polite">
        <div className="home-summary__headline">
          <p className="ops-eyebrow">首页 · 今日状态</p>
          <span className="home-summary__status-icon" aria-hidden="true"><StatusIcon size={22} /></span>
          <h1>{error ? '暂时无法确认影音中心状态' : summary.headline}</h1>
          <p>{error || summary.detail}</p>
          <small>{summary.generatedAt ? `最近读取 ${formatTimeAgo(summary.generatedAt)}` : '等待第一份状态证据'}</small>
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
            查看任务中心 <ArrowRight aria-hidden="true" size={16} />
          </button>
        </div>
      </section>

      <section className="home-metrics" aria-label="今日媒体处理统计">
        {metricDefinitions.map(({ key, label, icon: Icon }) => (
          <article className={`home-metric home-metric--${key}`} key={key}>
            <span aria-hidden="true"><Icon size={17} /></span>
            <small>{label}</small>
            <strong>{summary.counts[key]}</strong>
          </article>
        ))}
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
                onClick={() => onNavigate('tasks', issue.chainId || issue.targetKey ? {
                  chainId: issue.chainId || undefined,
                  targetKey: issue.targetKey || undefined,
                  title: issue.title
                } : undefined)}
              >
                <span className="home-issue__marker" aria-hidden="true" />
                <span className="home-issue__copy">
                  <strong>{issue.headline || issue.displayTitle || issue.title}</strong>
                  <small>
                    {issue.reasonText || '查看任务详情'}
                    {issue.secondaryReasonText && ` · ${issue.secondaryReasonText}`}
                    {' · '}{issue.observedAt ? formatTimeAgo(issue.observedAt) : '时间未知'}
                  </small>
                </span>
                <HealthBadge state={issue.healthState} />
                <ArrowRight aria-hidden="true" size={16} />
              </button>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
