import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  Library,
  ListChecks,
  Radio,
  Search,
  ShieldCheck,
  X
} from 'lucide-react';
import { getSubscriptionCalendarDateDetail, getSubscriptionCalendarSummary } from '../../services/api';
import type {
  SubscriptionCalendarDayPreview,
  SubscriptionCalendarDaySummary,
  SubscriptionCalendarEntry,
  SubscriptionCalendarStatus,
  SubscriptionHealthState
} from '../../types/subscriptions';
import { handleHorizontalTabKeyDown } from '../../utils/keyboardNavigation';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import type { AppNavigate } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';

interface CalendarPageProps {
  onNavigate: AppNavigate;
}

type CalendarMediaType = 'all' | 'movie' | 'tv';
type CalendarView = 'month' | 'week';
type CalendarStatus = 'all' | SubscriptionCalendarStatus;
type CalendarPosterItem = Pick<SubscriptionCalendarEntry, 'posterUrl' | 'title'> | Pick<SubscriptionCalendarDayPreview, 'posterUrl' | 'title'>;

const weekdays = ['一', '二', '三', '四', '五', '六', '日'];

function toDateKey(year: number, month: number, day: number) {
  return String(year).padStart(4, '0') + '-' + String(month).padStart(2, '0') + '-' + String(day).padStart(2, '0');
}

function dateParts(key: string) {
  const [year, month, day] = key.split('-').map(Number);
  return { year, month, day };
}

function shiftDateKey(key: string, days: number) {
  const { year, month, day } = dateParts(key);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return toDateKey(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
}

function weekStart(key: string) {
  const { year, month, day } = dateParts(key);
  const date = new Date(Date.UTC(year, month - 1, day));
  const offset = (date.getUTCDay() + 6) % 7;
  return shiftDateKey(key, -offset);
}

function formatEvidenceTime(value?: string) {
  if (!value) return '暂无证据';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(parsed);
}

function shanghaiDateKey(value: Date) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value || '';
  return part('year') + '-' + part('month') + '-' + part('day');
}

function entrySeasonNumber(entry: SubscriptionCalendarEntry) {
  if (entry.seasonNumber != null) return entry.seasonNumber;
  const match = /^S(\d+)/i.exec(entry.episodeLabel);
  return match ? Number(match[1]) : null;
}

function entryStatus(entry: SubscriptionCalendarEntry, todayKey: string): Exclude<CalendarStatus, 'all'> {
  if (entry.status) return entry.status;
  if (entry.libraryAt || entry.inLibrary) return 'library';
  if (entry.acquiredAt) return 'acquiring';
  return entry.date < todayKey ? 'unknown' : 'upcoming';
}

const statusLabel: Record<CalendarStatus, string> = {
  all: '全部', upcoming: '待播出', acquiring: '正在获取', library: '已入库', protected: '正常保护', missing: '逾期未获取', unknown: '状态未知'
};

const statusHealth: Record<Exclude<CalendarStatus, 'all'>, SubscriptionHealthState> = {
  upcoming: 'waiting', acquiring: 'waiting', library: 'normal', protected: 'protected', missing: 'action_required', unknown: 'evidence_insufficient'
};

function EntryPoster({ entry }: { entry: CalendarPosterItem }) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [entry.posterUrl]);
  if (entry.posterUrl && !imageFailed) {
    return <img alt="" aria-hidden="true" className="calendar-entry__poster" decoding="async" loading="lazy" src={entry.posterUrl} onError={() => setImageFailed(true)} />;
  }
  return <span aria-hidden="true" className="calendar-entry__poster calendar-entry__poster--fallback">{entry.title.charAt(0)}</span>;
}

export function CalendarPage({ onNavigate }: CalendarPageProps) {
  const now = new Date();
  const todayKey = shanghaiDateKey(now);
  const today = dateParts(todayKey);
  const [year, setYear] = useState(today.year);
  const [month, setMonth] = useState(today.month);
  const [days, setDays] = useState<SubscriptionCalendarDaySummary[]>([]);
  const [detailEntries, setDetailEntries] = useState<SubscriptionCalendarEntry[]>([]);
  const [mediaType, setMediaType] = useState<CalendarMediaType>('all');
  const [calendarView, setCalendarView] = useState<CalendarView>('month');
  const [status, setStatus] = useState<CalendarStatus>('all');
  const [query, setQuery] = useState('');
  const [selectedDate, setSelectedDate] = useState(todayKey);
  const [detailDate, setDetailDate] = useState('');
  const [mode, setMode] = useState<'loading' | 'live' | 'error'>('loading');
  const [detailMode, setDetailMode] = useState<'idle' | 'loading' | 'live' | 'error'>('idle');
  const [calendarErrors, setCalendarErrors] = useState<string[]>([]);
  const detailRequestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setMode('loading');
    setCalendarErrors([]);
    getSubscriptionCalendarSummary(year, month, mediaType, { signal: controller.signal })
      .then((payload) => {
        setDays(payload.calendar.days ?? []);
        setCalendarErrors(payload.calendar.errors ?? []);
        setMode('live');
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setDays([]);
          setMode('error');
        }
      });
    return () => controller.abort();
  }, [mediaType, month, year]);

  useEffect(() => () => detailRequestRef.current?.abort(), []);

  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN');
  const visibleDays = useMemo(() => days.filter((day) => {
    const matchesStatus = status === 'all' || day.statusCounts[status] > 0;
    const matchesQuery = !normalizedQuery || day.preview.some((entry) => (
      (entry.title + ' ' + entry.episodeLabel).toLocaleLowerCase('zh-CN').includes(normalizedQuery)
    ));
    return matchesStatus && matchesQuery;
  }), [days, normalizedQuery, status]);

  const daysByDate = useMemo(() => new Map(visibleDays.map((day) => [day.date, day])), [visibleDays]);

  const monthCells = useMemo(() => {
    const firstOffset = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
    const count = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const cells: Array<string | null> = [
      ...Array.from({ length: firstOffset }, () => null),
      ...Array.from({ length: count }, (_, index) => toDateKey(year, month, index + 1))
    ];
    while (cells.length % 7) cells.push(null);
    return cells;
  }, [month, year]);

  const visibleCells = useMemo(() => {
    if (calendarView === 'month') return monthCells;
    const start = weekStart(selectedDate);
    return Array.from({ length: 7 }, (_, index) => shiftDateKey(start, index));
  }, [calendarView, monthCells, selectedDate]);

  const shiftPeriod = (delta: number) => {
    if (calendarView === 'month') {
      const next = new Date(Date.UTC(year, month - 1 + delta, 1));
      const nextKey = toDateKey(next.getUTCFullYear(), next.getUTCMonth() + 1, 1);
      setYear(next.getUTCFullYear());
      setMonth(next.getUTCMonth() + 1);
      setSelectedDate(nextKey);
      return;
    }
    const nextKey = shiftDateKey(selectedDate, delta * 7);
    const next = dateParts(nextKey);
    setSelectedDate(nextKey);
    setYear(next.year);
    setMonth(next.month);
  };

  const goToday = () => {
    setYear(today.year);
    setMonth(today.month);
    setSelectedDate(todayKey);
  };

  const openDate = (dateKey: string) => {
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setSelectedDate(dateKey);
    setDetailDate(dateKey);
    setDetailEntries([]);
    setDetailMode('loading');
    getSubscriptionCalendarDateDetail(dateKey, mediaType, { signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setDetailEntries(payload.calendar.entries);
        setDetailMode('live');
      })
      .catch(() => {
        if (!controller.signal.aborted) setDetailMode('error');
      });
  };

  const closeDetail = () => {
    detailRequestRef.current?.abort();
    setDetailDate('');
    setDetailEntries([]);
    setDetailMode('idle');
  };

  const selectedEntries = useMemo(() => detailEntries.filter((entry) => {
    const currentStatus = entryStatus(entry, todayKey);
    const matchesStatus = status === 'all' || currentStatus === status;
    const matchesQuery = !normalizedQuery || (
      entry.title + ' ' + entry.episodeLabel
    ).toLocaleLowerCase('zh-CN').includes(normalizedQuery);
    return matchesStatus && matchesQuery;
  }), [detailEntries, normalizedQuery, status, todayKey]);

  const counts = days.reduce((result, day) => ({
    upcoming: result.upcoming + day.statusCounts.upcoming,
    acquiring: result.acquiring + day.statusCounts.acquiring,
    library: result.library + day.statusCounts.library,
    protected: result.protected + (day.statusCounts.protected ?? 0),
    missing: result.missing + day.statusCounts.missing,
    unknown: result.unknown + (day.statusCounts.unknown ?? 0)
  }), { upcoming: 0, acquiring: 0, library: 0, protected: 0, missing: 0, unknown: 0 });
  const totalEntries = days.reduce((total, day) => total + day.total, 0);

  return (
    <main className="work-page work-page--fill ops-page ops-page--calendar">
      <section className="ops-hero ops-hero--calendar">
        <div>
          <p className="ops-eyebrow">播出 · 获取 · 入库</p>
          <h1>日历</h1>
          <p className="ops-page-subtitle">什么时候播、何时开始获取、何时真正可看。</p>
          <p className="ops-deck">只有明确到具体季集的证据才会改变状态；季包完成不会批量标记单集。</p>
        </div>
        <div className="ops-calendar-stats" aria-label="本月追更统计">
          <div><Radio size={15} /><span>待播出</span><strong>{counts.upcoming}</strong></div>
          <div><Clock3 size={15} /><span>正在获取</span><strong>{counts.acquiring}</strong></div>
          <div><Library size={15} /><span>已入库</span><strong>{counts.library}</strong></div>
          <div className="is-protected"><ShieldCheck size={15} /><span>正常保护</span><strong>{counts.protected}</strong></div>
          <div className={counts.missing ? 'is-alert' : undefined}><ListChecks size={15} /><span>逾期未获取</span><strong>{counts.missing}</strong></div>
          <div className="is-unknown"><CircleHelp size={15} /><span>状态未知</span><strong>{counts.unknown}</strong></div>
        </div>
      </section>

      <section className="ops-panel ops-calendar-board calendar-board">
        <header className="calendar-board__head">
          <div className="ops-calendar-title">
            <span><CalendarDays size={17} /></span>
            <div><small>{calendarView === 'month' ? '月视图' : '周视图'}</small><h2>{year} 年 {month} 月</h2></div>
          </div>
          <div className="ops-calendar-controls">
            <div className="ops-calendar-type" role="tablist" aria-label="日历视图">
              {([['month', '月'], ['week', '周']] as const).map(([value, label]) => (
                <button aria-selected={calendarView === value} className={calendarView === value ? 'is-active' : undefined} key={value} role="tab" tabIndex={calendarView === value ? 0 : -1} type="button" onClick={() => setCalendarView(value)} onKeyDown={handleHorizontalTabKeyDown}>{label}</button>
              ))}
            </div>
            <button aria-label="上一周期" className="ops-icon-button" title="上一周期" type="button" onClick={() => shiftPeriod(-1)}><ChevronLeft aria-hidden="true" size={14} /></button>
            <button className="tool-link" type="button" onClick={goToday}>今天</button>
            <button aria-label="下一周期" className="ops-icon-button" title="下一周期" type="button" onClick={() => shiftPeriod(1)}><ChevronRight aria-hidden="true" size={14} /></button>
            <span className="ops-calendar-mode">{mode === 'loading' ? '加载中…' : mode === 'error' ? '日历不可用' : totalEntries + ' 条真实记录'}</span>
          </div>
        </header>

        <div className="calendar-filterbar">
          <label className="calendar-search"><Search aria-hidden="true" size={14} /><input aria-label="搜索日历作品" placeholder="搜索本月预览作品" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <div className="ops-calendar-type" role="tablist" aria-label="媒体类型">
            {([['all', '全部'], ['tv', '电视剧'], ['movie', '电影']] as const).map(([value, label]) => (
              <button aria-selected={mediaType === value} className={mediaType === value ? 'is-active' : undefined} key={value} role="tab" tabIndex={mediaType === value ? 0 : -1} type="button" onClick={() => setMediaType(value)} onKeyDown={handleHorizontalTabKeyDown}>{label}</button>
            ))}
          </div>
          <div className="calendar-status-filters" role="tablist" aria-label="日历状态">
            {(Object.keys(statusLabel) as CalendarStatus[]).map((value) => (
              <button aria-selected={status === value} className={status === value ? 'is-active' : undefined} key={value} role="tab" tabIndex={status === value ? 0 : -1} type="button" onClick={() => setStatus(value)} onKeyDown={handleHorizontalTabKeyDown}>{statusLabel[value]}</button>
            ))}
          </div>
        </div>

        {calendarErrors.length > 0 && <p className="ops-calendar-error">部分追更缺少播出日期，当前只显示可验证记录。</p>}
        {mode === 'error' && <p className="ops-calendar-error">日历与任务证据暂时无法读取，没有显示缓存或示例数据。</p>}
        {mode === 'live' && days.length === 0 && <div className="calendar-empty"><CalendarDays size={22} /><strong>当前月份没有追更日历</strong><span>切换月份或前往发现页添加追更。</span></div>}

        {days.length > 0 && (
          <div className="ops-calendar-scroll">
            <div aria-label={year + ' 年 ' + month + ' 月追更日历'} className={calendarView === 'week' ? 'calendar-grid calendar-grid--week' : 'calendar-grid'} role="grid">
              {weekdays.map((day) => <div className="calendar-grid__weekday" key={day} role="columnheader">{day}</div>)}
              {visibleCells.map((dateKey, index) => {
                if (!dateKey) return <div aria-hidden="true" className="calendar-cell calendar-cell--empty" key={'empty-' + index} />;
                const day = daysByDate.get(dateKey);
                const preview = (day?.preview ?? []).filter((entry) => (
                  (status === 'all' || entry.status === status)
                  && (!normalizedQuery || (entry.title + ' ' + entry.episodeLabel).toLocaleLowerCase('zh-CN').includes(normalizedQuery))
                ));
                const outsideMonth = dateParts(dateKey).month !== month;
                const cellClass = (dateKey === todayKey ? 'calendar-cell calendar-cell--today' : 'calendar-cell') + (outsideMonth ? ' calendar-cell--outside' : '');
                return (
                  <div className={cellClass} key={dateKey} role="gridcell">
                    <button className="calendar-cell__date" type="button" onClick={() => openDate(dateKey)}>{dateParts(dateKey).day}</button>
                    {day && (
                      <button aria-label={dateKey + '，共 ' + day.total + ' 条'} className="calendar-cell__mobile-summary" type="button" onClick={() => openDate(dateKey)}>
                        <span className={day.statusCounts.library ? 'is-library' : undefined} />
                        <span className={day.statusCounts.acquiring ? 'is-acquiring' : undefined} />
                        <span className={day.statusCounts.protected ? 'is-protected' : undefined} />
                        <span className={day.statusCounts.missing ? 'is-missing' : undefined} />
                        <span className={day.statusCounts.unknown ? 'is-unknown' : undefined} />
                        <b>{day.total}</b>
                      </button>
                    )}
                    {preview.slice(0, 3).map((entry) => (
                      <button className={'calendar-entry calendar-entry--' + entry.status} key={(entry.key || entry.title) + '-' + entry.episodeLabel} type="button" onClick={() => openDate(dateKey)}>
                        <EntryPoster entry={entry} />
                        <span className="calendar-entry__text"><strong>{entry.status === 'library' && <Check aria-hidden="true" size={11} />}{entry.title}</strong><small>{entry.episodeLabel} · {statusLabel[entry.status]}</small></span>
                      </button>
                    ))}
                    {day && day.total > preview.slice(0, 3).length && <button className="calendar-cell__more" type="button" onClick={() => openDate(dateKey)}>查看 {day.total} 条</button>}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <footer className="ops-calendar-legend">
          <span><i className="is-upcoming" />待播出</span><span><i className="is-acquiring" />正在获取</span><span><i className="is-library" />已入库</span><span><i className="is-protected" />正常保护</span><span><i className="is-overdue" />逾期未获取</span><span><i className="is-unknown" />状态未知</span><strong>时区：Asia/Shanghai</strong>
        </footer>
      </section>

      <ConfirmDialog labelledBy="calendar-detail-title" open={Boolean(detailDate)} onClose={closeDetail}>
        <div className="ops-confirm-dialog__signal"><CalendarDays aria-hidden="true" size={18} /></div>
        <button aria-label="关闭当日详情" className="calendar-detail__close" title="关闭" type="button" onClick={closeDetail}><X size={16} /></button>
        <h2 id="calendar-detail-title">{detailDate || '当日'} · {detailMode === 'live' ? selectedEntries.length + ' 条' : '读取中'}</h2>
        <p>每条记录分别展示播出、获取和入库证据；季级完成不会替代单集证据。</p>
        <div className="calendar-detail-list">
          {detailMode === 'loading' && <div className="calendar-empty"><strong>正在读取当日详情</strong></div>}
          {detailMode === 'error' && <div className="calendar-empty"><strong>当日详情读取失败</strong><span>关闭后重新打开该日期。</span></div>}
          {detailMode === 'live' && selectedEntries.map((entry) => {
            const currentStatus = entryStatus(entry, todayKey);
            return (
              <article className="calendar-detail-item" key={(entry.key || entry.title) + '-' + entry.episodeLabel}>
                <header><div><strong>{entry.title}</strong><small>{entry.episodeLabel}{entry.episodeTitle ? ' · ' + entry.episodeTitle : ''}</small></div><HealthBadge state={currentStatus === 'missing' || currentStatus === 'unknown' || currentStatus === 'protected' ? statusHealth[currentStatus] : entry.healthState || statusHealth[currentStatus]} /></header>
                <div className="calendar-evidence-times">
                  <span><b>播出</b><strong>{entry.date}</strong><small>TMDB 日历</small></span>
                  <span><b>获取</b><strong>{formatEvidenceTime(entry.acquiredAt)}</strong><small>{entry.acquisitionSource || '该集证据不足'}</small></span>
                  <span><b>入库</b><strong>{formatEvidenceTime(entry.libraryAt)}</strong><small>{entry.librarySource || (entry.inLibrary ? '媒体库文件' : '尚无该集证据')}</small></span>
                </div>
                {entry.reasonText && <p className="calendar-detail-item__reason">{entry.reasonText}</p>}
                <footer>
                  <button className="ops-action-button" type="button" onClick={() => onNavigate('subscriptions', { mediaType: entry.mediaType === 'movie' ? 'movie' : 'tv', subscriptionId: entry.key, tmdbId: entry.tmdbId, title: entry.title, seasonNumber: entrySeasonNumber(entry) })}>查看追更</button>
                  <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => onNavigate('tasks', { mediaType: entry.mediaType === 'movie' ? 'movie' : 'tv', chainId: entry.chainId, targetKey: entry.targetKey, subscriptionId: entry.key, tmdbId: entry.tmdbId, title: entry.title, seasonNumber: entrySeasonNumber(entry) })}>查看任务</button>
                </footer>
              </article>
            );
          })}
          {detailMode === 'live' && selectedEntries.length === 0 && <div className="calendar-empty"><strong>当天没有符合筛选的记录</strong><span>清除状态或作品筛选后再查看。</span></div>}
        </div>
      </ConfirmDialog>
    </main>
  );
}
