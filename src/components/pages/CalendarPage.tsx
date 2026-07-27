import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  Filter,
  Library,
  ListChecks,
  Radio,
  Search,
  ShieldCheck,
  X
} from 'lucide-react';
import { currentHistoryEntryIs, writeUrlQuery, type UrlHistoryMode } from '../../app/urlState';
import {
  getSubscriptionCalendarDateDetail,
  getSubscriptionCalendarRangeSummary,
  getSubscriptionCalendarSummary
} from '../../services/api';
import type {
  SubscriptionCalendarDayPreview,
  SubscriptionCalendarDaySummary,
  SubscriptionCalendarEntry,
  SubscriptionCalendarStatus,
  SubscriptionHealthState
} from '../../types/subscriptions';
import { handleHorizontalTabKeyDown } from '../../utils/keyboardNavigation';
import type { AppNavigate } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';

interface CalendarPageProps {
  onNavigate: AppNavigate;
}

type CalendarMediaType = 'all' | 'movie' | 'tv';
type CalendarView = 'month' | 'week';
type CalendarStatus = 'all' | SubscriptionCalendarStatus;
type CalendarPosterItem = Pick<SubscriptionCalendarEntry, 'posterUrl' | 'title'> | Pick<SubscriptionCalendarDayPreview, 'posterUrl' | 'title'>;
type CalendarUrlState = {
  year: number;
  month: number;
  view: CalendarView;
  mediaType: CalendarMediaType;
  status: CalendarStatus;
  query: string;
  date: string;
  detailOpen: boolean;
};
type CalendarUrlPatch = Partial<Omit<CalendarUrlState, 'date'>> & { date?: string | null };

const calendarDateHistoryKind = 'calendar:date';
const calendarRequestOptions = (signal: AbortSignal) => ({ signal, timeoutMs: 45_000 });

const weekdays = ['一', '二', '三', '四', '五', '六', '日'];

function toDateKey(year: number, month: number, day: number) {
  return String(year).padStart(4, '0') + '-' + String(month).padStart(2, '0') + '-' + String(day).padStart(2, '0');
}

function dateParts(key: string) {
  const [year, month, day] = key.split('-').map(Number);
  return { year, month, day };
}

function validDateKey(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return '';
  const { year, month, day } = dateParts(value);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() + 1 === month && parsed.getUTCDate() === day ? value : '';
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
  if (entry.libraryAt) return 'library';
  if (entry.acquiredAt) return 'acquiring';
  return entry.date < todayKey ? 'unknown' : 'upcoming';
}

const statusLabel: Record<CalendarStatus, string> = {
  all: '全部',
  upcoming: '待播出',
  acquiring: '正在获取',
  library: '整理入库完成',
  playable: '已可播放',
  protected: '正常保护',
  missing: '逾期未获取',
  unknown: '证据不足',
  unlinked: '状态未关联'
};

const statusHealth: Record<Exclude<CalendarStatus, 'all'>, SubscriptionHealthState> = {
  upcoming: 'waiting',
  acquiring: 'waiting',
  library: 'normal',
  playable: 'normal',
  protected: 'protected',
  missing: 'action_required',
  unknown: 'evidence_insufficient',
  unlinked: 'evidence_insufficient'
};
const mobilePrimaryStatuses: CalendarStatus[] = ['all', 'upcoming', 'acquiring', 'playable', 'missing'];
const mobileAdvancedStatuses: CalendarStatus[] = ['library', 'protected', 'unknown', 'unlinked'];

function readCalendarUrlState(todayKey: string, location: Location = window.location): CalendarUrlState {
  const params = new URLSearchParams(location.search);
  const date = validDateKey(params.get('date'));
  const dateFallback = date ? dateParts(date) : dateParts(todayKey);
  const parsedYear = Number(params.get('year'));
  const parsedMonth = Number(params.get('month'));
  const year = Number.isInteger(parsedYear) && parsedYear >= 1970 && parsedYear <= 2200 ? parsedYear : dateFallback.year;
  const month = Number.isInteger(parsedMonth) && parsedMonth >= 1 && parsedMonth <= 12 ? parsedMonth : dateFallback.month;
  const view = params.get('view') === 'week' ? 'week' : 'month';
  const mediaType = params.get('type') === 'movie' ? 'movie' : params.get('type') === 'tv' ? 'tv' : 'all';
  const statusValue = params.get('status');
  const status = statusValue && Object.prototype.hasOwnProperty.call(statusLabel, statusValue) ? statusValue as CalendarStatus : 'all';
  return {
    year,
    month,
    view,
    mediaType,
    status,
    query: params.get('q') ?? '',
    date,
    detailOpen: Boolean(date && params.get('detail') === '1')
  };
}

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
  const [initialUrlState] = useState(() => readCalendarUrlState(todayKey));
  const [year, setYear] = useState(initialUrlState.year);
  const [month, setMonth] = useState(initialUrlState.month);
  const [days, setDays] = useState<SubscriptionCalendarDaySummary[]>([]);
  const [searchIndex, setSearchIndex] = useState<SubscriptionCalendarDayPreview[]>([]);
  const [detailEntries, setDetailEntries] = useState<SubscriptionCalendarEntry[]>([]);
  const [mediaType, setMediaType] = useState<CalendarMediaType>(initialUrlState.mediaType);
  const [calendarView, setCalendarView] = useState<CalendarView>(initialUrlState.view);
  const [status, setStatus] = useState<CalendarStatus>(initialUrlState.status);
  const [query, setQuery] = useState(initialUrlState.query);
  const [selectedDate, setSelectedDate] = useState(
    initialUrlState.date || (initialUrlState.year === today.year && initialUrlState.month === today.month
      ? todayKey
      : toDateKey(initialUrlState.year, initialUrlState.month, 1))
  );
  const [detailDate, setDetailDate] = useState(initialUrlState.detailOpen ? initialUrlState.date : '');
  const [mode, setMode] = useState<'loading' | 'live' | 'error'>('loading');
  const [detailMode, setDetailMode] = useState<'idle' | 'loading' | 'live' | 'error'>('idle');
  const [calendarErrors, setCalendarErrors] = useState<string[]>([]);
  const [unlinkedCount, setUnlinkedCount] = useState(0);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const detailRequestRef = useRef<AbortController | null>(null);
  const detailPanelRef = useRef<HTMLElement | null>(null);
  const mobileFilterTriggerRef = useRef<HTMLButtonElement | null>(null);
  const mobileFilterSheetRef = useRef<HTMLElement | null>(null);
  const visibleWeekStart = calendarView === 'week' ? weekStart(selectedDate) : '';
  const visibleWeekEnd = visibleWeekStart ? shiftDateKey(visibleWeekStart, 6) : '';
  const includeUnlinked = status === 'unlinked';

  const writeCalendarUrlState = (patch: CalendarUrlPatch, mode: UrlHistoryMode, entryKind?: string) => {
    const nextDate = Object.prototype.hasOwnProperty.call(patch, 'date') ? patch.date || '' : selectedDate;
    const nextDetailOpen = Object.prototype.hasOwnProperty.call(patch, 'detailOpen')
      ? Boolean(patch.detailOpen)
      : Boolean(detailDate);
    writeUrlQuery({
      year: patch.year ?? year,
      month: patch.month ?? month,
      view: patch.view ?? calendarView,
      type: patch.mediaType ?? mediaType,
      status: patch.status ?? status,
      q: patch.query ?? query,
      date: nextDate || null,
      detail: nextDetailOpen && nextDate ? 1 : null
    }, mode, entryKind);
  };

  useEffect(() => {
    const controller = new AbortController();
    setMode('loading');
    setCalendarErrors([]);
    const request = calendarView === 'week'
      ? getSubscriptionCalendarRangeSummary(
          visibleWeekStart, visibleWeekEnd, mediaType, calendarRequestOptions(controller.signal), includeUnlinked
        )
      : getSubscriptionCalendarSummary(
          year, month, mediaType, calendarRequestOptions(controller.signal), includeUnlinked
        );
    request
      .then((payload) => {
        if (controller.signal.aborted) return;
        setDays(payload.calendar.days ?? []);
        setSearchIndex(payload.calendar.searchIndex ?? []);
        setCalendarErrors(payload.calendar.errors ?? []);
        setUnlinkedCount(payload.calendar.stats.unlinked ?? 0);
        setMode('live');
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setDays([]);
          setSearchIndex([]);
          setUnlinkedCount(0);
          setMode('error');
        }
      });
    return () => controller.abort();
  }, [calendarView, includeUnlinked, mediaType, month, visibleWeekEnd, visibleWeekStart, year]);

  const requestDateDetail = (
    dateKey: string,
    requestedMediaType: CalendarMediaType,
    requestedIncludeUnlinked = includeUnlinked
  ) => {
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setSelectedDate(dateKey);
    setDetailDate(dateKey);
    setDetailEntries([]);
    setDetailMode('loading');
    getSubscriptionCalendarDateDetail(
      dateKey, requestedMediaType, calendarRequestOptions(controller.signal), requestedIncludeUnlinked
    )
      .then((payload) => {
        if (controller.signal.aborted) return;
        setDetailEntries(payload.calendar.entries);
        setDetailMode('live');
      })
      .catch(() => {
        if (!controller.signal.aborted) setDetailMode('error');
      });
  };

  useEffect(() => {
    const applyUrlState = () => {
      const next = readCalendarUrlState(todayKey);
      setYear(next.year);
      setMonth(next.month);
      setCalendarView(next.view);
      setMediaType(next.mediaType);
      setStatus(next.status);
      setQuery(next.query);
      setSelectedDate(next.date || (next.year === today.year && next.month === today.month
        ? todayKey
        : toDateKey(next.year, next.month, 1)));
      if (next.detailOpen && next.date) {
        requestDateDetail(next.date, next.mediaType, next.status === 'unlinked');
      } else {
        detailRequestRef.current?.abort();
        setDetailDate('');
        setDetailEntries([]);
        setDetailMode('idle');
      }
    };

    if (initialUrlState.detailOpen && initialUrlState.date) {
      requestDateDetail(
        initialUrlState.date,
        initialUrlState.mediaType,
        initialUrlState.status === 'unlinked'
      );
    }
    window.addEventListener('popstate', applyUrlState);
    return () => {
      detailRequestRef.current?.abort();
      window.removeEventListener('popstate', applyUrlState);
    };
  }, []);

  useEffect(() => {
    if (!mobileFiltersOpen) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focusableSelector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const frame = window.requestAnimationFrame(() => {
      mobileFilterSheetRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMobileFiltersOpen(false);
        return;
      }
      if (event.key !== 'Tab' || !mobileFilterSheetRef.current) return;
      const focusable = Array.from(
        mobileFilterSheetRef.current.querySelectorAll<HTMLElement>(focusableSelector)
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!mobileFilterSheetRef.current.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const wideViewport = window.matchMedia('(min-width: 701px)');
    const handleWideViewport = () => {
      if (wideViewport.matches) setMobileFiltersOpen(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    wideViewport.addEventListener('change', handleWideViewport);
    return () => {
      window.cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
      wideViewport.removeEventListener('change', handleWideViewport);
      (previouslyFocused ?? mobileFilterTriggerRef.current)?.focus({ preventScroll: true });
    };
  }, [mobileFiltersOpen]);

  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN');
  const filteredIndex = useMemo(() => searchIndex.filter((entry) => (
    (status === 'all' || entry.status === status)
    && (!normalizedQuery || (entry.title + ' ' + entry.episodeLabel).toLocaleLowerCase('zh-CN').includes(normalizedQuery))
  )), [normalizedQuery, searchIndex, status]);
  const filteredIndexByDate = useMemo(() => {
    const grouped = new Map<string, SubscriptionCalendarDayPreview[]>();
    filteredIndex.forEach((entry) => {
      if (!entry.date) return;
      grouped.set(entry.date, [...(grouped.get(entry.date) ?? []), entry]);
    });
    return grouped;
  }, [filteredIndex]);
  const visibleDays = useMemo(() => days.filter((day) => {
    const matchesStatus = status === 'all' || day.statusCounts[status] > 0;
    const matchesQuery = !normalizedQuery || filteredIndexByDate.has(day.date);
    return matchesStatus && matchesQuery;
  }), [days, filteredIndexByDate, normalizedQuery, status]);

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
      writeCalendarUrlState({ year: next.getUTCFullYear(), month: next.getUTCMonth() + 1, date: nextKey, detailOpen: false }, 'push');
      return;
    }
    const nextKey = shiftDateKey(selectedDate, delta * 7);
    const next = dateParts(nextKey);
    setSelectedDate(nextKey);
    setYear(next.year);
    setMonth(next.month);
    writeCalendarUrlState({ year: next.year, month: next.month, date: nextKey, detailOpen: false }, 'push');
  };

  const goToday = () => {
    setYear(today.year);
    setMonth(today.month);
    setSelectedDate(todayKey);
    writeCalendarUrlState({ year: today.year, month: today.month, date: todayKey, detailOpen: false }, 'push');
  };

  const openDate = (dateKey: string) => {
    writeCalendarUrlState({ date: dateKey, detailOpen: true }, 'push', calendarDateHistoryKind);
    requestDateDetail(dateKey, mediaType);
  };

  const closeDetail = () => {
    detailRequestRef.current?.abort();
    setDetailDate('');
    setDetailEntries([]);
    setDetailMode('idle');
    if (currentHistoryEntryIs(calendarDateHistoryKind)) {
      window.history.back();
    } else {
      writeCalendarUrlState({ date: selectedDate, detailOpen: false }, 'replace');
    }
  };

  useEffect(() => {
    if (!detailDate) return undefined;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    const focusableSelector = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    document.body.style.overflow = 'hidden';
    const frame = window.requestAnimationFrame(() => {
      detailPanelRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeDetail();
        return;
      }
      if (event.key !== 'Tab' || !detailPanelRef.current) return;
      const focusable = Array.from(
        detailPanelRef.current.querySelectorAll<HTMLElement>(focusableSelector)
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!detailPanelRef.current.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
      previouslyFocused?.focus({ preventScroll: true });
    };
  }, [detailDate]);

  const changeCalendarView = (next: CalendarView) => {
    setCalendarView(next);
    writeCalendarUrlState({ view: next }, 'replace');
  };

  const changeMediaType = (next: CalendarMediaType) => {
    setMediaType(next);
    writeCalendarUrlState({ mediaType: next }, 'replace');
  };

  const changeStatus = (next: CalendarStatus) => {
    setStatus(next);
    writeCalendarUrlState({ status: next }, 'replace');
  };

  const changeQuery = (next: string) => {
    setQuery(next);
    writeCalendarUrlState({ query: next }, 'replace');
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
    playable: result.playable + (day.statusCounts.playable ?? 0),
    protected: result.protected + (day.statusCounts.protected ?? 0),
    missing: result.missing + day.statusCounts.missing,
    unknown: result.unknown + (day.statusCounts.unknown ?? 0),
    unlinked: result.unlinked + (day.statusCounts.unlinked ?? 0)
  }), { upcoming: 0, acquiring: 0, library: 0, playable: 0, protected: 0, missing: 0, unknown: 0, unlinked: 0 });
  const totalEntries = days.reduce((total, day) => total + day.total, 0);
  const isLoading = mode === 'loading';

  return (
    <main className="work-page work-page--fill ops-page ops-page--calendar">
      <section className="ops-hero ops-hero--calendar ops-hero--compact">
        <div>
          <p className="ops-eyebrow">播出 · 获取 · 整理 · 可播放</p>
          <h1>日历</h1>
          <p className="ops-page-subtitle">什么时候播、何时开始获取、何时真正可看。</p>
          <p className="ops-deck">只有明确到具体季集的证据才会改变状态；季包完成不会批量标记单集。</p>
        </div>
        <div className="ops-calendar-stats" aria-label={calendarView === 'week' ? '本周追更统计' : '本月追更统计'}>
          <div><Radio size={15} /><span>待播出</span><strong>{isLoading ? '—' : counts.upcoming}</strong></div>
          <div><Clock3 size={15} /><span>正在获取</span><strong>{isLoading ? '—' : counts.acquiring}</strong></div>
          <div><Check size={15} /><span>已可播放</span><strong>{isLoading ? '—' : counts.playable}</strong></div>
          <div><Library size={15} /><span>整理入库</span><strong>{isLoading ? '—' : counts.library}</strong></div>
          <div className={counts.missing ? 'is-alert' : undefined}><ListChecks size={15} /><span>逾期未获取</span><strong>{isLoading ? '—' : counts.missing}</strong></div>
          <div className="is-protected"><ShieldCheck size={15} /><span>正常保护</span><strong>{isLoading ? '—' : counts.protected}</strong></div>
          <div className="is-faint"><CircleHelp size={15} /><span>状态未关联</span><strong>{isLoading ? '—' : unlinkedCount}</strong></div>
        </div>
      </section>

      <section className="ops-panel ops-calendar-board calendar-board">
        <header className="calendar-board__head">
          <div className="ops-calendar-title">
            <span><CalendarDays size={17} /></span>
            <div><small>{calendarView === 'month' ? '月视图' : '周视图'}</small><h2>{year} 年 {month} 月</h2></div>
          </div>
          <div className="ops-calendar-controls">
            <div className="ops-calendar-type ops-calendar-view-switch" role="tablist" aria-label="日历视图">
              {([['month', '月'], ['week', '周']] as const).map(([value, label]) => (
                <button aria-selected={calendarView === value} className={calendarView === value ? 'is-active' : undefined} key={value} role="tab" tabIndex={calendarView === value ? 0 : -1} type="button" onClick={() => changeCalendarView(value)} onKeyDown={handleHorizontalTabKeyDown}>{label}</button>
              ))}
            </div>
            <button aria-label="上一周期" className="ops-icon-button" title="上一周期" type="button" onClick={() => shiftPeriod(-1)}><ChevronLeft aria-hidden="true" size={14} /></button>
            <button className="tool-link" type="button" onClick={goToday}>今天</button>
            <button aria-label="下一周期" className="ops-icon-button" title="下一周期" type="button" onClick={() => shiftPeriod(1)}><ChevronRight aria-hidden="true" size={14} /></button>
            <span className="ops-calendar-mode">{mode === 'loading' ? '加载中…' : mode === 'error' ? '日历不可用' : totalEntries + ' 条真实记录'}</span>
          </div>
        </header>

        <div className="calendar-filterbar">
          <label className="calendar-search"><Search aria-hidden="true" size={14} /><input aria-label="搜索日历作品" placeholder="搜索当前范围作品" value={query} onChange={(event) => changeQuery(event.target.value)} /></label>
          <div className="ops-calendar-type" role="tablist" aria-label="媒体类型">
            {([['all', '全部'], ['tv', '电视剧'], ['movie', '电影']] as const).map(([value, label]) => (
              <button aria-selected={mediaType === value} className={mediaType === value ? 'is-active' : undefined} key={value} role="tab" tabIndex={mediaType === value ? 0 : -1} type="button" onClick={() => changeMediaType(value)} onKeyDown={handleHorizontalTabKeyDown}>{label}</button>
            ))}
          </div>
          <div className="calendar-status-filters" role="tablist" aria-label="日历状态">
            {(Object.keys(statusLabel) as CalendarStatus[]).map((value) => (
              <button aria-selected={status === value} className={status === value ? 'is-active' : undefined} key={value} role="tab" tabIndex={status === value ? 0 : -1} type="button" onClick={() => changeStatus(value)} onKeyDown={handleHorizontalTabKeyDown}>{statusLabel[value]}</button>
            ))}
          </div>
        </div>
        <div className="ops-mobile-filter-summary ops-mobile-filter-summary--calendar">
          <span>
            <small>当前筛选</small>
            <strong>{calendarView === 'month' ? '月视图' : '周视图'} · {mediaType === 'all' ? '全部类型' : mediaType === 'tv' ? '电视剧' : '电影'} · {statusLabel[status]}{query ? ` · “${query}”` : ''}</strong>
          </span>
          <button
            aria-controls="calendar-mobile-filter-sheet"
            aria-expanded={mobileFiltersOpen}
            className="ops-mobile-filter-button"
            ref={mobileFilterTriggerRef}
            type="button"
            onClick={() => setMobileFiltersOpen(true)}
          >
            <Filter aria-hidden="true" size={15} />筛选
          </button>
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
                const filteredPreview = filteredIndexByDate.get(dateKey) ?? [];
                const filtersActive = Boolean(normalizedQuery || status !== 'all');
                const preview = filtersActive ? filteredPreview : (day?.preview ?? []);
                const filteredCount = filtersActive ? filteredPreview.length : day?.total ?? 0;
                const filteredStatusCounts = filtersActive
                  ? filteredPreview.reduce((result, entry) => ({ ...result, [entry.status]: result[entry.status] + 1 }), {
                      upcoming: 0,
                      acquiring: 0,
                      library: 0,
                      playable: 0,
                      protected: 0,
                      missing: 0,
                      unknown: 0,
                      unlinked: 0
                    })
                  : day?.statusCounts;
                const mobilePoster = preview[0];
                const outsideMonth = dateParts(dateKey).month !== month;
                const cellClass = (dateKey === todayKey ? 'calendar-cell calendar-cell--today' : 'calendar-cell') + (outsideMonth ? ' calendar-cell--outside' : '');
                return (
                  <div className={cellClass} key={dateKey} role="gridcell">
                    <button className="calendar-cell__date" type="button" onClick={() => openDate(dateKey)}>{dateParts(dateKey).day}</button>
                    {filteredCount > 0 && (
                      <button aria-label={dateKey + '，当前筛选共 ' + filteredCount + ' 条'} className="calendar-cell__mobile-summary" type="button" onClick={() => openDate(dateKey)}>
                        {mobilePoster && <EntryPoster entry={mobilePoster} />}
                        <span aria-hidden="true" className="calendar-cell__mobile-states">
                          <i className={filteredStatusCounts?.library ? 'is-library' : undefined} />
                          <i className={filteredStatusCounts?.playable ? 'is-playable' : undefined} />
                          <i className={filteredStatusCounts?.acquiring ? 'is-acquiring' : undefined} />
                          <i className={filteredStatusCounts?.protected ? 'is-protected' : undefined} />
                          <i className={filteredStatusCounts?.missing ? 'is-missing' : undefined} />
                          <i className={filteredStatusCounts?.unknown ? 'is-unknown' : undefined} />
                          <i className={filteredStatusCounts?.unlinked ? 'is-unlinked' : undefined} />
                        </span>
                        <b>{filteredCount}</b>
                      </button>
                    )}
                    {preview.slice(0, 3).map((entry) => (
                      <button className={'calendar-entry calendar-entry--' + entry.status} key={(entry.key || entry.title) + '-' + entry.episodeLabel} type="button" onClick={() => openDate(dateKey)}>
                        <EntryPoster entry={entry} />
                        <span className="calendar-entry__text"><strong>{['library', 'playable'].includes(entry.status) && <Check aria-hidden="true" size={11} />}{entry.title}</strong><small>{entry.episodeLabel} · {statusLabel[entry.status]}</small></span>
                      </button>
                    ))}
                    {filteredCount > preview.slice(0, 3).length && <button className="calendar-cell__more" type="button" onClick={() => openDate(dateKey)}>查看 {filteredCount} 条</button>}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <footer className="ops-calendar-legend">
          <span><i className="is-upcoming" />待播出</span><span><i className="is-acquiring" />正在获取</span><span><i className="is-library" />整理入库</span><span><i className="is-playable" />已可播放</span><span><i className="is-protected" />正常保护</span><span><i className="is-overdue" />逾期未获取</span><span><i className="is-unknown" />证据不足</span><strong>时区：Asia/Shanghai</strong>
        </footer>
      </section>

      {mobileFiltersOpen && (
        <div className="ops-filter-sheet-backdrop" onPointerDown={(event) => {
          if (event.target === event.currentTarget) setMobileFiltersOpen(false);
        }}>
          <section
            aria-labelledby="calendar-mobile-filter-title"
            aria-modal="true"
            className="ops-filter-sheet"
            id="calendar-mobile-filter-sheet"
            ref={mobileFilterSheetRef}
            role="dialog"
            tabIndex={-1}
          >
            <div aria-hidden="true" className="ops-filter-sheet__handle" />
            <header className="ops-filter-sheet__header">
              <div><small>日历</small><h2 id="calendar-mobile-filter-title">筛选日历</h2></div>
              <button aria-label="关闭筛选" className="ops-filter-sheet__close" type="button" onClick={() => setMobileFiltersOpen(false)}><X aria-hidden="true" size={18} /></button>
            </header>
            <div className="ops-filter-sheet__body">
              <label className="ops-filter-sheet__search"><Search aria-hidden="true" size={15} /><input aria-label="搜索日历作品" placeholder="输入作品或集数" value={query} onChange={(event) => changeQuery(event.target.value)} /></label>
              <fieldset className="ops-filter-sheet__group">
                <legend>时间视图</legend>
                <div className="ops-filter-sheet__options ops-filter-sheet__options--two">
                  {([['month', '月视图'], ['week', '周视图']] as const).map(([value, label]) => (
                    <button aria-pressed={calendarView === value} className={calendarView === value ? 'is-active' : undefined} key={value} type="button" onClick={() => changeCalendarView(value)}>{label}</button>
                  ))}
                </div>
              </fieldset>
              <fieldset className="ops-filter-sheet__group">
                <legend>作品类型</legend>
                <div className="ops-filter-sheet__options ops-filter-sheet__options--three">
                  {([['all', '全部'], ['tv', '电视剧'], ['movie', '电影']] as const).map(([value, label]) => (
                    <button aria-pressed={mediaType === value} className={mediaType === value ? 'is-active' : undefined} key={value} type="button" onClick={() => changeMediaType(value)}>{label}</button>
                  ))}
                </div>
              </fieldset>
              <fieldset className="ops-filter-sheet__group">
                <legend>状态</legend>
                <div className="ops-filter-sheet__options">
                  {mobilePrimaryStatuses.map((value) => (
                    <button aria-pressed={status === value} className={status === value ? 'is-active' : undefined} key={value} type="button" onClick={() => changeStatus(value)}>{statusLabel[value]}</button>
                  ))}
                </div>
              </fieldset>
              <fieldset className="ops-filter-sheet__group">
                <legend>高级项</legend>
                <div className="ops-filter-sheet__options ops-filter-sheet__options--two">
                  {mobileAdvancedStatuses.map((value) => (
                    <button aria-pressed={status === value} className={status === value ? 'is-active' : undefined} key={value} type="button" onClick={() => changeStatus(value)}>{statusLabel[value]}</button>
                  ))}
                </div>
              </fieldset>
            </div>
            <footer className="ops-filter-sheet__footer">
              <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => setMobileFiltersOpen(false)}>完成</button>
            </footer>
          </section>
        </div>
      )}

      {detailDate && (
        <div className="calendar-detail-backdrop" onPointerDown={(event) => {
          if (event.target === event.currentTarget) closeDetail();
        }}>
          <aside
            aria-labelledby="calendar-detail-title"
            aria-modal="true"
            className="calendar-detail-panel"
            ref={detailPanelRef}
            role="dialog"
          >
            <header className="calendar-detail-panel__header">
              <div className="calendar-detail-panel__signal"><CalendarDays aria-hidden="true" size={18} /></div>
              <div>
                <small>当日详情</small>
                <h2 id="calendar-detail-title">{detailDate} · {detailMode === 'live' ? selectedEntries.length + ' 条' : '读取中'}</h2>
                <p>播出、获取和入库证据按作品分别展示。</p>
              </div>
              <button aria-label="关闭当日详情" className="calendar-detail__close" title="关闭" type="button" onClick={closeDetail}><X size={16} /></button>
            </header>
            <div className="calendar-detail-list">
          {detailMode === 'loading' && <div className="calendar-empty"><strong>正在读取当日详情</strong></div>}
          {detailMode === 'error' && <div className="calendar-empty"><strong>当日详情读取失败</strong><span>关闭后重新打开该日期。</span></div>}
          {detailMode === 'live' && selectedEntries.map((entry) => {
            const currentStatus = entryStatus(entry, todayKey);
            return (
              <article className="calendar-detail-item" key={(entry.key || entry.title) + '-' + entry.episodeLabel}>
                <header>
                  <EntryPoster entry={entry} />
                  <div><strong>{entry.title}</strong><small>{entry.episodeLabel}{entry.episodeTitle ? ' · ' + entry.episodeTitle : ''}</small></div>
                  <HealthBadge state={['missing', 'unknown', 'protected', 'unlinked'].includes(currentStatus) ? statusHealth[currentStatus] : entry.healthState || statusHealth[currentStatus]} />
                </header>
                <div className="calendar-evidence-times">
                  <span><b>播出</b><strong>{entry.date}</strong><small>TMDB 日历</small></span>
                  <span><b>获取</b><strong>{formatEvidenceTime(entry.acquiredAt)}</strong><small>{entry.acquisitionSource || '该集证据不足'}</small></span>
                  <span><b>入库</b><strong>{formatEvidenceTime(entry.libraryAt)}</strong><small>{entry.librarySource || '尚无该集证据'}</small></span>
                  <span><b>可播放</b><strong>{formatEvidenceTime(entry.playableAt)}</strong><small>{entry.playableSource || '尚无 Emby 集级证据'}</small></span>
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
          </aside>
        </div>
      )}
    </main>
  );
}
