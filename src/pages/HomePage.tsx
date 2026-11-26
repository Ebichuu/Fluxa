import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Clock3,
  CloudUpload,
  Download,
  ExternalLink,
  Pause,
  Play,
  RefreshCw,
  Tv,
} from 'lucide-react';
import { api } from '../services/api';
import type { ActionImpactPreview, ActionableIssue, DashboardResponse, MediaType, SaRecordRef } from '../types/api';
import { ConfirmImpactModal } from '../components/ConfirmImpactModal';
import { SaRecoveryModal } from '../components/SaRecoveryModal';
import { StatusPill } from '../components/StatusPill';

interface HomePageProps {
  onOpenMedia: (type: MediaType, tmdbId: string) => void;
  onGoSettings: () => void;
}

function fmtSpeed(bytesPerSec: number): string {
  if (!bytesPerSec || bytesPerSec <= 0) return '0 B/s';
  if (bytesPerSec >= 1024 * 1024) return `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
  if (bytesPerSec >= 1024) return `${(bytesPerSec / 1024).toFixed(0)} KB/s`;
  return `${bytesPerSec} B/s`;
}

function weekdayLabel(iso: string): string {
  const names = ['日', '一', '二', '三', '四', '五', '六'];
  const d = new Date(`${iso}T00:00:00+08:00`);
  return `周${names[d.getDay()] || ''}`;
}

export function HomePage({ onOpenMedia, onGoSettings }: HomePageProps) {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [calDays, setCalDays] = useState<7 | 28>(7);
  const [selectedDate, setSelectedDate] = useState('');
  const [toast, setToast] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [pendingAction, setPendingAction] = useState<{
    actionType: string;
    payload: Record<string, unknown>;
    preview: ActionImpactPreview;
  } | null>(null);
  const [saRecoveryRecord, setSaRecoveryRecord] = useState<SaRecordRef | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  const load = useCallback(async (days = calDays, refresh = false) => {
    setLoading(true);
    setError('');
    try {
      const res = await api.getDashboard(days, '', refresh);
      setData(res);
      setSelectedDate((cur) => cur || res.calendar.today);
    } catch (err) {
      setError(err instanceof Error ? err.message : '首页数据加载失败');
    } finally {
      setLoading(false);
    }
  }, [calDays]);

  useEffect(() => {
    void load(calDays, false);
  }, [calDays, load]);

  // If backend returned instant SQLite cache and triggered a background refresh, quietly re-read once after 2.5s
  useEffect(() => {
    if (!data?.cache?.refreshing) return;
    const timer = window.setTimeout(() => {
      void load(calDays, false);
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [data?.cache?.refreshing, data?.generatedAt, calDays, load]);

  const runBoundedAction = async (actionType: string, payload: Record<string, unknown>) => {
    setToast(null);
    try {
      const preview = await api.previewAction(actionType, payload);
      if (preview.requiresConfirm) {
        setPendingAction({ actionType, payload, preview });
        return;
      }
      setActionBusy(true);
      const res = await api.executeAction(actionType, payload, true);
      setToast({ tone: 'ok', text: res.message || '操作已执行' });
      await load(calDays);
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '操作失败' });
    } finally {
      setActionBusy(false);
    }
  };

  const confirmPending = async () => {
    if (!pendingAction) return;
    setActionBusy(true);
    try {
      const res = await api.executeAction(pendingAction.actionType, pendingAction.payload, true);
      setToast({ tone: 'ok', text: res.message || '操作已执行' });
      setPendingAction(null);
      await load(calDays);
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '操作失败' });
    } finally {
      setActionBusy(false);
    }
  };

  const handleMuteIssue = async (issue: ActionableIssue) => {
    try {
      await api.muteIssue(issue.issueKey);
      setToast({ tone: 'ok', text: '已忽略该条异常提醒' });
      await load(calDays);
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '忽略失败' });
    }
  };

  const openMediaKey = (mediaKey: string) => {
    const [t, id] = mediaKey.split(':');
    if ((t === 'movie' || t === 'tv') && id) onOpenMedia(t, id);
  };

  const activeDay = data?.calendar.days.find((d) => d.date === selectedDate) || data?.calendar.days.find((d) => d.isToday);
  const anyServiceConfigured = data?.services.some((s) => s.configured);
  const issueCount = data?.counts.issues || 0;
  const activeCount = (data?.counts.activeDownloads || 0) + (data?.counts.activeUploads || 0);

  const heroTone = issueCount > 0 ? 'is-error' : activeCount > 0 ? 'is-active' : '';
  const heroHeadline = !data
    ? '正在汇总影音链路状态…'
    : !anyServiceConfigured
      ? '先连接你的 NAS 影音服务'
      : issueCount > 0
        ? `有 ${issueCount} 项任务卡住或报错，建议处理`
        : activeCount > 0
          ? `当前 ${activeCount} 个任务处理中，无阻塞异常`
          : '链路运转顺畅，当前无阻塞异常';

  return (
    <div className="stack">
      {/* Compact v1-style Glass Hero + Inline Service Strip */}
      <section className="hero-bar">
        <div className="hero-left">
          <div className={`hero-icon-ring ${heroTone}`}>
            {issueCount > 0 ? <AlertTriangle size={20} /> : activeCount > 0 ? <Clock3 size={20} /> : <CheckCircle2 size={20} />}
          </div>
          <div>
            <p className="hero-eyebrow">
              首页 · 追更日历与链路进度
              {data?.generatedAt ? ` · 快照 ${data.generatedAt.slice(11, 19)}` : ''}
              {data?.cache?.refreshing ? '（后台同步中…）' : data?.cache?.fromCache ? '（本地缓存秒开）' : ''}
            </p>
            <h1 className="hero-title">{heroHeadline}</h1>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {data && !anyServiceConfigured && (
            <button className="btn btn-primary" type="button" onClick={onGoSettings}>配置服务连接</button>
          )}
          <button className="btn" type="button" onClick={() => void load(calDays, true)} disabled={loading}>
            <RefreshCw size={14} /> {loading ? '刷新中…' : '刷新'}
          </button>
        </div>

        <div className="service-strip" style={{ width: '100%', paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          {(data?.services || []).map((svc) => (
            <a
              key={svc.service}
              className="service-chip"
              href={svc.webUrl || undefined}
              target={svc.webUrl ? '_blank' : undefined}
              rel="noreferrer"
              title={svc.message}
              onClick={(e) => {
                if (!svc.configured) {
                  e.preventDefault();
                  onGoSettings();
                }
              }}
            >
              <span className={`service-dot ${svc.status}`} />
              <strong>{svc.name}</strong>
              <span className="muted">{svc.configured ? (svc.connected ? '在线' : '异常') : '未配置'}</span>
              {svc.webUrl && <ExternalLink size={12} className="muted" />}
            </a>
          ))}
        </div>
      </section>

      {toast && <div className={`banner ${toast.tone}`}>{toast.text}</div>}
      {error && <div className="banner error">{error}</div>}

      {/* 4 Core KPI Chips */}
      <div className="grid-4">
        <div className="kpi-card">
          <span className="muted">今日追更</span>
          <span className="kpi-value">{data ? data.counts.todayUpdates : '-'}</span>
          <span className="muted">部/集今日播出</span>
        </div>
        <div className="kpi-card">
          <span className="muted">今日入 Emby</span>
          <span className="kpi-value" style={{ color: 'var(--ok)' }}>{data ? data.counts.inEmbyToday : '-'}</span>
          <span className="muted">条新媒体库条目</span>
        </div>
        <div className="kpi-card">
          <span className="muted">下载 / 上传中</span>
          <span className="kpi-value" style={{ color: 'var(--active)' }}>
            {data ? `${data.counts.activeDownloads} / ${data.counts.activeUploads}` : '-'}
          </span>
          <span className="muted">qB 活动 / 115 上传</span>
        </div>
        <div className="kpi-card">
          <span className="muted">需要处理</span>
          <span className="kpi-value" style={{ color: issueCount > 0 ? 'var(--err)' : 'var(--ok)' }}>
            {data ? issueCount : '-'}
          </span>
          <span className="muted">项卡住或失败任务</span>
        </div>
      </div>

      {/* Actionable Issues Inbox (Pain Point 3) */}
      <section className="card">
        <div className="card-header">
          <h2 className="card-title">
            {issueCount > 0 ? <AlertTriangle size={17} color="var(--err)" /> : <CheckCircle2 size={17} color="var(--ok)" />}
            异常待办
          </h2>
          <span className="muted">仅展示真实报错与卡顿；SA 权重规则拒绝不误报为异常</span>
        </div>
        {!data?.issues.length ? (
          <div className="muted">当前没有需要人工处理的阻塞异常。</div>
        ) : (
          <div className="list">
            {data.issues.map((issue) => (
              <div key={issue.issueKey} className={`list-row list-row--${issue.severity}`}>
                <div className="row-main">
                  <div className="row-title">
                    <StatusPill status={issue.severity} label={issue.stageLabel} />
                    <span>{issue.title}</span>
                    {issue.episodeLabel && <span className="pill idle">{issue.episodeLabel}</span>}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 13.5 }}>{issue.errorText}</div>
                  {issue.subtitle && issue.subtitle !== issue.title && (
                    <div className="muted" style={{ marginTop: 2, wordBreak: 'break-all' }}>{issue.subtitle}</div>
                  )}
                </div>
                <div className="row-actions">
                  {issue.saRecord && (
                    <button
                      className="btn btn-sm btn-primary"
                      type="button"
                      onClick={() => setSaRecoveryRecord(issue.saRecord || null)}
                    >
                      诊断与恢复
                    </button>
                  )}
                  {issue.mediaKey && (
                    <button className="btn btn-sm" type="button" onClick={() => openMediaKey(issue.mediaKey)}>
                      查全链路
                    </button>
                  )}
                  {issue.retryAction && (
                    <button
                      className="btn btn-sm"
                      type="button"
                      disabled={actionBusy}
                      onClick={() => void runBoundedAction(issue.retryAction!.actionType, issue.retryAction!.payload)}
                    >
                      {issue.retryAction.label}
                    </button>
                  )}
                  {issue.appUrl && (
                    <a className="btn btn-sm" href={issue.appUrl} target="_blank" rel="noreferrer">
                      {issue.appLabel} <ExternalLink size={12} />
                    </a>
                  )}
                  <button className="btn btn-sm" type="button" onClick={() => void handleMuteIssue(issue)}>
                    忽略
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Poster-Rich Following Calendar */}
      <section className="card">
        <div className="card-header">
          <h2 className="card-title"><CalendarDays size={17} color="var(--accent)" /> 追更日历</h2>
          <div className="tabs" style={{ marginBottom: 0 }}>
            <button
              className={`tab-btn ${calDays === 7 ? 'active' : ''}`}
              type="button"
              onClick={() => setCalDays(7)}
            >
              近 7 天
            </button>
            <button
              className={`tab-btn ${calDays === 28 ? 'active' : ''}`}
              type="button"
              onClick={() => setCalDays(28)}
            >
              近 4 周
            </button>
          </div>
        </div>

        {data?.calendar.truncated && data.calendar.truncationWarning && (
          <div className="banner warn" style={{ marginBottom: 10 }}>
            <AlertTriangle size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
            {data.calendar.truncationWarning}
          </div>
        )}
        {data?.calendar.embyTruncated && data.calendar.embyTruncationWarning && (
          <div className="banner warn" style={{ marginBottom: 10 }}>
            <AlertTriangle size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
            {data.calendar.embyTruncationWarning}
          </div>
        )}

        <div className="cal-strip">
          {(data?.calendar.days || []).map((day) => (
            <button
              key={day.date}
              type="button"
              className={`cal-day ${day.isToday ? 'today' : ''} ${day.date === activeDay?.date ? 'selected' : ''}`}
              onClick={() => setSelectedDate(day.date)}
            >
              <span className="cal-date">
                <span>{day.date.slice(5)}</span>
                <span>{day.isToday ? '今天' : weekdayLabel(day.date)}</span>
              </span>
              {day.items.length > 0 && (
                <div className="cal-mini-posters">
                  {day.items.slice(0, 3).map((ep) =>
                    ep.posterUrl ? (
                      <img
                        key={`${ep.mediaKey}-${ep.episodeLabel}`}
                        className={`cal-mini-poster state-${ep.state}`}
                        src={ep.posterUrl}
                        alt={ep.title}
                        title={`${ep.title} ${ep.episodeLabel}`}
                      />
                    ) : (
                      <span
                        key={`${ep.mediaKey}-${ep.episodeLabel}`}
                        className={`cal-mini-poster state-${ep.state}`}
                        title={`${ep.title} ${ep.episodeLabel}`}
                      >
                        {ep.title.slice(0, 1)}
                      </span>
                    )
                  )}
                </div>
              )}
              <span className="cal-count">{day.items.length > 0 ? `${day.items.length} 集更新` : '无更新'}</span>
            </button>
          ))}
        </div>

        <div style={{ marginTop: 14 }}>
          <div className="muted" style={{ marginBottom: 8 }}>
            {activeDay ? `${activeDay.date} 追更清单（点击任意条目进入 5 步全链路透视）` : '暂无日历数据'}
          </div>
          {!activeDay?.items.length ? (
            <div className="muted">该日暂无订阅剧集播出（需配置 TMDB 与 MoviePilot Neo / Torra 订阅）。</div>
          ) : (
            <div className="list">
              {activeDay.items.map((ep) => (
                <div
                  key={`${ep.mediaKey}-${ep.episodeLabel}`}
                  className="list-row list-row--clickable"
                  onClick={() => onOpenMedia(ep.mediaType, ep.tmdbId)}
                >
                  <div style={{ display: 'flex', gap: 12, flex: 1, minWidth: 0, alignItems: 'center' }}>
                    {ep.posterUrl && <img className="poster-thumb" src={ep.posterUrl} alt={ep.title} />}
                    <div className="row-main">
                      <div className="row-title">
                        <span>{ep.title}</span>
                        <span className="pill idle">{ep.episodeLabel}</span>
                        <StatusPill status={ep.state} label={ep.stateLabel} />
                      </div>
                      {ep.episodeName && <div className="muted" style={{ marginTop: 2 }}>{ep.episodeName}</div>}
                    </div>
                  </div>
                  <button className="btn btn-sm" type="button">查看 5 步状态</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Progress Panel: Active Downloads/Uploads + Today's Emby Entries */}
      <div className="grid-2">
        <section className="card">
          <div className="card-header">
            <h2 className="card-title"><Download size={17} color="var(--active)" /> 下载与 115 上传进度</h2>
          </div>
          {!data?.activeDownloads.length && !data?.activeUploads.length ? (
            <div className="muted">当前没有进行中的 qB 下载或 Torra 115 上传任务。</div>
          ) : (
            <div className="list">
              {(data?.activeDownloads || []).map((t) => (
                <div key={t.hash} className="list-row">
                  <div className="row-main">
                    <div className="row-title">
                      <StatusPill
                        status={t.status === 'missing_files' || t.status === 'error' ? 'error' : t.status === 'stalled' ? 'warn' : 'active'}
                        label={t.statusLabel}
                      />
                      <span style={{ wordBreak: 'break-all' }}>{t.name}</span>
                    </div>
                    <div className="muted" style={{ marginTop: 4 }}>
                      {t.progressPercent}% · ↓ {fmtSpeed(t.dlspeed)} · ↑ {fmtSpeed(t.upspeed)}
                    </div>
                    <div className="progress-track">
                      <div
                        className={`progress-fill ${t.status === 'missing_files' || t.status === 'error' ? 'error' : t.status === 'stalled' ? 'warn' : ''}`}
                        style={{ width: `${Math.min(100, t.progressPercent)}%` }}
                      />
                    </div>
                  </div>
                  <div className="row-actions">
                    {t.status === 'paused' ? (
                      <button
                        className="btn btn-sm"
                        type="button"
                        disabled={actionBusy}
                        onClick={() => void runBoundedAction('qb_resume', { hash: t.hash, name: t.name })}
                      >
                        <Play size={12} /> 恢复
                      </button>
                    ) : (
                      <button
                        className="btn btn-sm"
                        type="button"
                        disabled={actionBusy}
                        onClick={() => void runBoundedAction('qb_pause', { hash: t.hash, name: t.name })}
                      >
                        <Pause size={12} /> 暂停
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {(data?.activeUploads || []).map((u) => (
                <div key={u.runId} className="list-row">
                  <div className="row-main">
                    <div className="row-title">
                      <CloudUpload size={14} color="var(--active)" />
                      <StatusPill status="active" label="115 上传中" />
                      <span>{u.taskKey || u.runId}</span>
                    </div>
                    <div className="muted">{u.message || 'Torra 正在执行 115 秒传/上传'}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card">
          <div className="card-header">
            <h2 className="card-title"><Tv size={17} color="var(--ok)" /> 今日已入 Emby</h2>
            <span className="muted">核对 Emby 条目存在（播放以客户端为准）</span>
          </div>
          {!data?.recentEmby.length ? (
            <div className="muted">今日暂无新进入 Emby 的条目。</div>
          ) : (
            <div className="list">
              {data.recentEmby.map((item, idx) => (
                <div
                  key={`${item.mediaKey}-${item.episodeLabel}-${idx}`}
                  className="list-row list-row--clickable"
                  onClick={() => onOpenMedia(item.mediaType, item.tmdbId)}
                >
                  <div className="row-main">
                    <div className="row-title">
                      <span>{item.title}</span>
                      <span className="pill ok">{item.episodeLabel}</span>
                      {item.isStrm && <span className="pill protected">STRM</span>}
                    </div>
                    <div className="muted">Emby 已有条目 · 点击查看完整链路</div>
                  </div>
                  {item.webUrl && (
                    <a
                      className="btn btn-sm"
                      href={item.webUrl}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Emby <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {pendingAction && (
        <ConfirmImpactModal
          preview={pendingAction.preview}
          busy={actionBusy}
          onCancel={() => setPendingAction(null)}
          onConfirm={() => void confirmPending()}
        />
      )}

      {saRecoveryRecord && (
        <SaRecoveryModal
          record={saRecoveryRecord}
          onClose={() => setSaRecoveryRecord(null)}
          onUpdated={() => void load(calDays)}
        />
      )}
    </div>
  );
}
