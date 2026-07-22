import { useEffect, useMemo, useState } from 'react';
import {
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Library,
  ListChecks,
  Radio,
  Search,
  X
} from 'lucide-react';
import { getSubscriptionCalendarTimeline } from '../../services/api';
import type { SubscriptionCalendarEntry, SubscriptionHealthState } from '../../types/subscriptions';
import { handleHorizontalTabKeyDown } from '../../utils/keyboardNavigation';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import type { AppNavigate } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';

interface CalendarPageProps {
  onNavigate: AppNavigate;
}

type CalendarMediaType = 'all' | 'movie' | 'tv';
type CalendarView = 'month' | 'week';
type CalendarStatus = 'all' | 'upcoming' | 'acquiring' | 'library' | 'missing';

const weekdays = ['一', '二', '三', '四', '五', '六', '日'];

function toDateKey(year: number, month: number, day: number) {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
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
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function entrySeasonNumber(entry: SubscriptionCalendarEntry) {
  if (entry.seasonNumber != null) return entry.seasonNumber;
  const match = /^S(\d+)/i.exec(entry.episodeLabel);
  return match ? Number(match[1]) : null;
}

function entryStatus(entry: SubscriptionCalendarEntry, todayKey: string): CalendarStatus {
  if (entry.libraryAt || entry.inLibrary) return 'library';
  if (entry.acquiredAt) return 'acquiring';
  return entry.date < todayKey ? 'missing' : 'upcoming';
}

const statusLabel: Record<CalendarStatus, string> = {
  all: '全部', upcoming: '待播出', acquiring: '正在获取', library: '已入库', missing: '逾期未获取'
};

const statusHealth: Record<Exclude<CalendarStatus, 'all'>, SubscriptionHealthState> = {
  upcoming: 'waiting', acquiring: 'waiting', library: 'normal', missing: 'action_required'
};

function EntryPoster({ entry }: { entry: SubscriptionCalendarEntry }) {
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
  const [entries, setEntries] = useState<SubscriptionCalendarEntry[]>([]);
  const [mediaType, setMediaType] = useState<CalendarMediaType>('all');
  const [calendarView, setCalendarView] = useState<CalendarView>('month');
  const [status, setStatus] = useState<CalendarStatus>('all');
  const [query, setQuery] = useState('');
  const [selectedDate, setSelectedDate] = useState(todayKey);
  const [detailDate, setDetailDate] = useState('');
  const [mode, setMode] = useState<'loading' | 'live' | 'error'>('loading');
  const [calendarErrors, setCalendarErrors] = useState<string[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    setMode('loading');
    setCalendarErrors([]);
    getSubscriptionCalendarTimeline(year, month, mediaType, { signal: controller.signal })
      .then((payload) => {
        setEntries(payload.calendar.entries);
        setCalendarErrors(payload.calendar.errors ?? []);
        setMode('live');
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setEntries([]);
          setMode('error');
        }
      });
    return () => controller.abort();
  }, [mediaType, month, year]);

  const filteredEntries = useMemo(() => entries.filter((entry) => {
    const matchesStatus = status === 'all' || entryStatus(entry, todayKey) === status;
    const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN');
    return matchesStatus && (!normalizedQuery || `${entry.title} ${entry.episodeLabel}`.toLocaleLowerCase('zh-CN').includes(normalizedQuery));
  }), [entries, query, status, todayKey]);

  const entriesByDate = useMemo(() => {
    const result = new Map<string, SubscriptionCalendarEntry[]>();
    for (const entry of filteredEntries) result.set(entry.date, [...(result.get(entry.date) ?? []), entry]);
    return result;
  }, [filteredEntries]);

  const monthCells = useMemo(() => {
    const firstOffset = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
    const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const cells: Array<string | null> = [
      ...Array.from({ length: firstOffset }, () => null),
      ...Array.from({ length: days }, (_, index) => toDateKey(year, month, index + 1))
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

  const selectedEntries = detailDate ? entriesByDate.get(detailDate) ?? [] : [];
  const counts = {
    upcoming: entries.filter((entry) => entryStatus(entry, todayKey) === 'upcoming').length,
    acquiring: entries.filter((entry) => entryStatus(entry, todayKey) === 'acquiring').length,
    library: entries.filter((entry) => entryStatus(entry, todayKey) === 'library').length,
    missing: entries.filter((entry) => entryStatus(entry, todayKey) === 'missing').length
  };

  return (
    <main className="work-page work-page--fill ops-page ops-page--calendar">
      <section className="ops-hero ops-hero--calendar">
        <div>
          <p className="ops-eyebrow">播出 · 获取 · 入库</p>
          <h1>日历</h1>
          <p className="ops-page-subtitle">什么时候播、何时开始获取、何时真正可看。</p>
          <p className="ops-deck">时间来自追更日历与统一任务证据；缺少证据时保持未知，不推断为成功。</p>
        </div>
        <div className="ops-calendar-stats" aria-label="本月追更统计">
          <div><Radio size={15} /><span>待播出</span><strong>{counts.upcoming}</strong></div>
          <div><Clock3 size={15} /><span>正在获取</span><strong>{counts.acquiring}</strong></div>
          <div><Library size={15} /><span>已入库</span><strong>{counts.library}</strong></div>
          <div className={counts.missing ? 'is-alert' : undefined}><ListChecks size={15} /><span>逾期未获取</span><strong>{counts.missing}</strong></div>
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
            <span className="ops-calendar-mode">{mode === 'loading' ? '加载中…' : mode === 'error' ? '日历不可用' : `${entries.length} 条真实记录`}</span>
          </div>
        </header>

        <div className="calendar-filterbar">
          <label className="calendar-search"><Search aria-hidden="true" size={14} /><input aria-label="搜索日历作品" placeholder="搜索作品或季集" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
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
        {mode === 'live' && entries.length === 0 && <div className="calendar-empty"><CalendarDays size={22} /><strong>当前月份没有追更日历</strong><span>切换月份或前往发现页添加追更。</span></div>}

        {entries.length > 0 && (
          <div className="ops-calendar-scroll">
            <div aria-label={`${year} 年 ${month} 月追更日历`} className={calendarView === 'week' ? 'calendar-grid calendar-grid--week' : 'calendar-grid'} role="grid">
              {weekdays.map((day) => <div className="calendar-grid__weekday" key={day} role="columnheader">{day}</div>)}
              {visibleCells.map((dateKey, index) => {
                if (!dateKey) return <div aria-hidden="true" className="calendar-cell calendar-cell--empty" key={`empty-${index}`} />;
                const dayEntries = entriesByDate.get(dateKey) ?? [];
                const outsideMonth = dateParts(dateKey).month !== month;
                return (
                  <div className={`${dateKey === todayKey ? 'calendar-cell calendar-cell--today' : 'calendar-cell'}${outsideMonth ? ' calendar-cell--outside' : ''}`} key={dateKey} role="gridcell">
                    <button className="calendar-cell__date" type="button" onClick={() => { setSelectedDate(dateKey); setDetailDate(dateKey); }}>{dateParts(dateKey).day}</button>
                    {dayEntries.slice(0, 3).map((entry) => {
                      const currentStatus = entryStatus(entry, todayKey) as Exclude<CalendarStatus, 'all'>;
                      return (
                        <button className={`calendar-entry calendar-entry--${currentStatus}`} key={`${entry.key}-${entry.episodeLabel}`} type="button" onClick={() => { setSelectedDate(dateKey); setDetailDate(dateKey); }}>
                          <EntryPoster entry={entry} />
                          <span className="calendar-entry__text"><strong>{currentStatus === 'library' && <Check aria-hidden="true" size={11} />}{entry.title}</strong><small>{entry.episodeLabel} · {statusLabel[currentStatus]}</small></span>
                        </button>
                      );
                    })}
                    {dayEntries.length > 3 && <button className="calendar-cell__more" type="button" onClick={() => { setSelectedDate(dateKey); setDetailDate(dateKey); }}>另有 {dayEntries.length - 3} 条</button>}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <footer className="ops-calendar-legend">
          <span><i className="is-upcoming" />待播出</span><span><i className="is-acquiring" />正在获取</span><span><i className="is-library" />已入库</span><span><i className="is-overdue" />需要排查</span><strong>时区：Asia/Shanghai</strong>
        </footer>
      </section>

      <ConfirmDialog labelledBy="calendar-detail-title" open={Boolean(detailDate)} onClose={() => setDetailDate('')}>
        <div className="ops-confirm-dialog__signal"><CalendarDays aria-hidden="true" size={18} /></div>
        <button aria-label="关闭当日详情" className="calendar-detail__close" title="关闭" type="button" onClick={() => setDetailDate('')}><X size={16} /></button>
        <h2 id="calendar-detail-title">{detailDate || '当日'} · {selectedEntries.length} 条</h2>
        <p>每条记录分别展示播出、获取证据和入库证据；没有证据的步骤保持未知。</p>
        <div className="calendar-detail-list">
          {selectedEntries.map((entry) => {
            const currentStatus = entryStatus(entry, todayKey) as Exclude<CalendarStatus, 'all'>;
            return (
              <article className="calendar-detail-item" key={`${entry.key}-${entry.episodeLabel}`}>
                <header><div><strong>{entry.title}</strong><small>{entry.episodeLabel}{entry.episodeTitle ? ` · ${entry.episodeTitle}` : ''}</small></div><HealthBadge state={entry.healthState || statusHealth[currentStatus]} /></header>
                <div className="calendar-evidence-times">
                  <span><b>播出</b><strong>{entry.date}</strong><small>TMDB 日历</small></span>
                  <span><b>获取</b><strong>{formatEvidenceTime(entry.acquiredAt)}</strong><small>{entry.acquisitionSource || '任务证据不足'}</small></span>
                  <span><b>入库</b><strong>{formatEvidenceTime(entry.libraryAt)}</strong><small>{entry.librarySource || '尚无完成证据'}</small></span>
                </div>
                {entry.reasonText && <p className="calendar-detail-item__reason">{entry.reasonText}</p>}
                <footer>
                  <button className="ops-action-button" type="button" onClick={() => onNavigate('subscriptions', { mediaType: entry.mediaType === 'movie' ? 'movie' : 'tv', subscriptionId: entry.key, tmdbId: entry.tmdbId, title: entry.title, seasonNumber: entrySeasonNumber(entry) })}>查看追更</button>
                  <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => onNavigate('tasks', { mediaType: entry.mediaType === 'movie' ? 'movie' : 'tv', chainId: entry.chainId, targetKey: entry.targetKey, subscriptionId: entry.key, tmdbId: entry.tmdbId, title: entry.title, seasonNumber: entrySeasonNumber(entry) })}>查看任务</button>
                </footer>
              </article>
            );
          })}
          {selectedEntries.length === 0 && <div className="calendar-empty"><strong>当天没有符合筛选的记录</strong><span>清除状态或作品筛选后再查看。</span></div>}
        </div>
      </ConfirmDialog>
    </main>
  );
}
