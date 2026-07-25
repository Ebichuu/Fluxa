import { useEffect, useState } from 'react';
import { ArrowRight, Bookmark, CalendarDays, Cloud, Download, Library, PlayCircle, RefreshCcw } from 'lucide-react';
import { getMediaOverview } from '../../services/api';
import type { MediaLifecycleStatus, MediaOverviewResponse } from '../../types/mediaSearch';
import type { AppNavigate, AppPathNavigate, TaskNavigationTarget } from '../layout/AppTopNav';
import { PosterImage } from '../layout/PosterImage';
import { RelativeTime } from '../status/RelativeTime';

interface MediaOverviewPageProps {
  target: TaskNavigationTarget | null;
  onNavigate: AppNavigate;
  onNavigatePath: AppPathNavigate;
}

function statusLabel(status: MediaLifecycleStatus | string) {
  const labels: Record<string, string> = {
    following: '追更中',
    not_following: '尚未追更',
    linked: '已同步 Torra',
    not_linked: '尚未同步',
    in_progress: '处理中',
    completed: '已完成',
    action_required: '需要处理',
    available: '可用',
    scheduled: '已排期',
    unknown: '待确认'
  };
  return labels[status] ?? '待确认';
}

function stageClass(status: string) {
  if (status === 'action_required') return 'is-danger';
  if (status === 'in_progress' || status === 'scheduled') return 'is-processing';
  if (['completed', 'available', 'following', 'linked'].includes(status)) return 'is-complete';
  return 'is-unknown';
}

export function MediaOverviewPage({ target, onNavigate, onNavigatePath }: MediaOverviewPageProps) {
  const [data, setData] = useState<MediaOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);
  const mediaKey = target?.mediaType && target.tmdbId ? `${target.mediaType}:${target.tmdbId}` : '';

  useEffect(() => {
    if (!mediaKey) {
      setError('作品链接缺少媒体类型或 TMDB 身份');
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setData(null);
    getMediaOverview(mediaKey, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        const message = reason instanceof Error ? reason.message : '';
        setError(/abort|signal/i.test(message) ? '作品状态读取被中断，请重试' : message || '作品总览读取失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [mediaKey, reloadToken]);

  if (loading) return <main className="work-page ops-page media-overview-page"><div className="ops-empty">正在汇总作品状态…</div></main>;
  if (error || !data) {
    return <main className="work-page ops-page media-overview-page"><div className="ops-empty"><strong>暂时无法打开作品总览</strong><span>{error}</span><button className="ops-action-button ops-action-button--primary" type="button" onClick={() => setReloadToken((value) => value + 1)}><RefreshCcw size={14} />重试</button></div></main>;
  }

  const navigationTarget: TaskNavigationTarget = {
    mediaType: data.media.mediaType,
    tmdbId: data.media.tmdbId,
    title: data.media.title,
    userState: data.userState
  };
  const lifecycle = [
    { key: 'subscription', label: '追更', icon: Bookmark, status: data.subscription.status, detail: data.subscription.seasonNumbers.length ? `第 ${data.subscription.seasonNumbers.join('、')} 季` : statusLabel(data.subscription.status), observedAt: data.subscription.lastCheckedAt },
    { key: 'torra', label: 'Torra', icon: Cloud, status: data.subscription.torraStatus, detail: statusLabel(data.subscription.torraStatus) },
    { key: 'download', label: '下载', icon: Download, status: data.download.status, detail: data.download.latestEpisode?.label || (data.download.completedTasks ? `${data.download.completedTasks} 个任务完成` : statusLabel(data.download.status)), observedAt: data.download.observedAt },
    { key: 'cloud115', label: '115', icon: Cloud, status: data.cloud115.status, detail: data.cloud115.status === 'completed' ? '已有逐任务证据' : statusLabel(data.cloud115.status), observedAt: data.cloud115.observedAt },
    { key: 'library', label: '入库', icon: Library, status: data.library.status, detail: data.library.latestEpisode?.label || statusLabel(data.library.status), observedAt: data.library.observedAt },
    { key: 'emby', label: 'Emby', icon: PlayCircle, status: data.emby.status, detail: data.emby.status === 'available' ? (data.emby.evidenceScope === 'episode' ? '已有集级可看证据' : '作品可看') : '尚未确认可看' }
  ];

  const runPrimaryAction = () => {
    if (data.primaryAction.kind === 'view_subscription') {
      onNavigate('subscriptions', navigationTarget);
      return;
    }
    onNavigate('tasks', navigationTarget);
  };
  const primaryActionLabel = data.primaryAction.kind === 'view_subscription'
    ? '查看追更'
    : '前往任务中心处理';

  return (
    <main className="work-page ops-page media-overview-page">
      <section className="media-overview-hero">
        <PosterImage className="media-overview-hero__poster" fallbackClassName="media-overview-hero__poster--fallback" src={data.media.posterUrl} title={data.media.title} />
        <div className="media-overview-hero__copy">
          <p className="ops-eyebrow">作品总览 · {data.media.mediaType === 'tv' ? '剧集' : '电影'}</p>
          <h1>{data.media.title}</h1>
          <small>{data.media.year ? `${data.media.year} · ` : ''}TMDB {data.media.tmdbId}</small>
          <p>{data.resultText}</p>
          <div className="media-overview-actions">
            {data.primaryAction.available && <button className="ops-action-button ops-action-button--primary" type="button" onClick={runPrimaryAction}>{primaryActionLabel}<ArrowRight size={14} /></button>}
            {(!data.primaryAction.available || data.primaryAction.kind === 'view_subscription') && <button className="ops-action-button" type="button" onClick={() => onNavigate('tasks', navigationTarget)}>查看任务</button>}
            <button className="ops-action-button" type="button" onClick={() => onNavigatePath(data.links.calendar)}>查看日历</button>
          </div>
        </div>
      </section>

      <section className="media-lifecycle" aria-label="作品处理生命周期">
        {lifecycle.map(({ key, label, icon: Icon, status, detail, observedAt }) => (
          <article className={`media-lifecycle__item ${stageClass(status)}`} key={key}>
            <span aria-hidden="true"><Icon size={16} /></span>
            <div><small>{label}</small><strong>{detail}</strong>{observedAt && <RelativeTime value={observedAt} />}</div>
          </article>
        ))}
      </section>

      <section className="media-overview-next">
        <div>
          <p className="ops-eyebrow">下一步</p>
          <h2>{data.userState === 'action_required' ? '这部作品需要处理' : data.userState === 'in_progress' ? '系统正在继续处理' : '当前无需人工介入'}</h2>
          <p>{data.primaryAction.reason}</p>
        </div>
        <div className="media-overview-next__links">
          <button type="button" onClick={() => onNavigate('subscriptions', navigationTarget)}>追更详情<ArrowRight size={14} /></button>
          <button type="button" onClick={() => onNavigate('tasks', navigationTarget)}>任务证据<ArrowRight size={14} /></button>
          <button type="button" onClick={() => onNavigatePath(data.links.calendar)}>日历排期<CalendarDays size={14} /></button>
        </div>
      </section>

      <details className="media-overview-diagnostics">
        <summary>高级诊断</summary>
        <dl>
          <div><dt>来源</dt><dd>{data.media.sources.join('、') || '未知'}</dd></div>
          <div><dt>日历记录</dt><dd>{data.calendar.entryCount} 条</dd></div>
          <div><dt>已入库日历记录</dt><dd>{data.calendar.inLibraryCount} 条</dd></div>
          <div><dt>下次更新</dt><dd>{data.calendar.nextEpisodeLabel || '待确认'}{data.calendar.nextAirAt && <> · <RelativeTime value={data.calendar.nextAirAt} /></>}</dd></div>
          <div><dt>播放证据</dt><dd>{data.playback.status === 'available' ? 'Emby 已确认可看；当前没有安全直链' : '待确认'}</dd></div>
          <div><dt>作品标识</dt><dd>{data.media.mediaKey}</dd></div>
        </dl>
      </details>
    </main>
  );
}
