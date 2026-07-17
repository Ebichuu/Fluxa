import { useEffect, useState } from 'react';
import { CalendarDays, Check, ChevronLeft, ChevronRight, Clock3, Library, Radio } from 'lucide-react';
import { getSubscriptionCalendar } from '../../services/api';
import type { SubscriptionCalendarEntry } from '../../types/subscriptions';
import { handleHorizontalTabKeyDown } from '../../utils/keyboardNavigation';
import type { PageId } from '../layout/AppTopNav';

interface CalendarPageProps {
  onNavigate: (page: PageId) => void;
}

type EntryState = '待播出' | '已入库' | '未入库';
type CalendarMediaType = 'all' | 'movie' | 'tv';

const weekdays = ['一', '二', '三', '四', '五', '六', '日'];

// 未配置 TMDB 或订阅为空时的示例数据（按 2026 年 7 月）
const sampleEntries: SubscriptionCalendarEntry[] = [
  { date: '2026-07-07', title: 'Severance', episodeLabel: 'S02E09', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-08', title: 'The Bear', episodeLabel: 'S03E05', posterUrl: '', inLibrary: true, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-08', title: 'House of the Dragon', episodeLabel: 'S02E04', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-09', title: 'The Boys', episodeLabel: 'S04E06', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-11', title: 'Sunny', episodeLabel: 'S01E01', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-11', title: 'Vikings: Valhalla', episodeLabel: 'S03E01', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-15', title: 'The Bear', episodeLabel: 'S03E06', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-22', title: 'The Bear', episodeLabel: 'S03E07', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' },
  { date: '2026-07-28', title: 'Slow Horses', episodeLabel: 'S04E01', posterUrl: '', inLibrary: false, mediaType: 'tv', sourceLabel: '' }
];

function toDateKey(year: number, month: number, day: number) {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function entryState(entry: SubscriptionCalendarEntry, todayKey: string): EntryState {
  if (entry.inLibrary) {
    return '已入库';
  }
  return entry.date < todayKey ? '未入库' : '待播出';
}

const entryClass: Record<EntryState, string> = {
  待播出: 'calendar-entry',
  已入库: 'calendar-entry calendar-entry--done',
  未入库: 'calendar-entry calendar-entry--stuck'
};

function EntryPoster({ entry }: { entry: SubscriptionCalendarEntry }) {
  if (entry.posterUrl) {
    return <img alt="" aria-hidden="true" className="calendar-entry__poster" src={entry.posterUrl} />;
  }
  return (
    <span aria-hidden="true" className="calendar-entry__poster calendar-entry__poster--fallback">
      {entry.title.charAt(0)}
    </span>
  );
}

export function CalendarPage({ onNavigate }: CalendarPageProps) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [entries, setEntries] = useState<SubscriptionCalendarEntry[]>([]);
  const [mediaType, setMediaType] = useState<CalendarMediaType>('all');
  const [mode, setMode] = useState<'loading' | 'live' | 'sample' | 'error'>('loading');
  const [calendarErrors, setCalendarErrors] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    setMode('loading');
    setCalendarErrors([]);

    getSubscriptionCalendar(year, month, mediaType)
      .then((payload) => {
        if (cancelled) {
          return;
        }
        if (payload.configured && payload.calendar) {
          setEntries(payload.calendar.entries);
          setCalendarErrors(payload.calendar.errors ?? []);
          setMode('live');
        } else {
          setEntries(sampleEntries.filter((entry) => entry.date.startsWith(toDateKey(year, month, 1).slice(0, 8))));
          setMode('sample');
        }
      })
      .catch(() => {
        if (!cancelled) {
          setEntries([]);
          setMode('error');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [mediaType, year, month]);

  const todayKey = toDateKey(now.getFullYear(), now.getMonth() + 1, now.getDate());
  const daysInMonth = new Date(year, month, 0).getDate();
  const firstDayOffset = (new Date(year, month - 1, 1).getDay() + 6) % 7; // 周一起始

  const cells: Array<number | null> = [
    ...Array.from({ length: firstDayOffset }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1)
  ];
  while (cells.length % 7 !== 0) {
    cells.push(null);
  }

  const entriesByDay = new Map<number, SubscriptionCalendarEntry[]>();
  for (const entry of entries) {
    const day = Number(entry.date.slice(8, 10));
    if (!Number.isFinite(day) || day < 1) {
      continue;
    }
    entriesByDay.set(day, [...(entriesByDay.get(day) ?? []), entry]);
  }

  const shiftMonth = (delta: number) => {
    const next = new Date(year, month - 1 + delta, 1);
    setYear(next.getFullYear());
    setMonth(next.getMonth() + 1);
  };

  const inLibraryCount = entries.filter((entry) => entry.inLibrary).length;
  const overdueCount = entries.filter((entry) => entryState(entry, todayKey) === '未入库').length;
  const upcomingCount = entries.filter((entry) => entryState(entry, todayKey) === '待播出').length;

  return (
    <main className="work-page work-page--fill ops-page ops-page--calendar">
      <section className="ops-hero ops-hero--calendar">
        <div>
          <p className="ops-eyebrow">日历 · 播出与入库</p>
          <h1>什么时候播，哪些还没入库。</h1>
          <p className="ops-deck">这里汇总订阅内容的播出日期和入库进度；逾期内容可以直接进入任务中心排查。</p>
        </div>
        <div className="ops-calendar-stats" aria-label="本月订阅统计">
          <div><Radio size={15} /><span>待播出</span><strong>{upcomingCount}</strong></div>
          <div><Library size={15} /><span>已入库</span><strong>{inLibraryCount}</strong></div>
          <div className={overdueCount ? 'is-alert' : undefined}><Clock3 size={15} /><span>逾期未入库</span><strong>{overdueCount}</strong></div>
        </div>
      </section>

      <section className="ops-panel ops-calendar-board calendar-board">
        <header className="calendar-board__head">
          <div className="ops-calendar-title">
            <span><CalendarDays size={17} /></span>
            <div><small>月历</small><h2>{year} 年 {month} 月</h2></div>
          </div>
          <div className="ops-calendar-controls">
          <div className="ops-calendar-type" role="tablist" aria-label="日历媒体类型">
            {([['all', '全部'], ['tv', '电视剧'], ['movie', '电影']] as const).map(([value, label]) => (
              <button
                aria-selected={mediaType === value}
                className={mediaType === value ? 'is-active' : undefined}
                key={value}
                role="tab"
                tabIndex={mediaType === value ? 0 : -1}
                type="button"
                onClick={() => setMediaType(value)}
                onKeyDown={handleHorizontalTabKeyDown}
              >{label}</button>
            ))}
          </div>
          <button aria-label="上个月" className="ops-icon-button" type="button" onClick={() => shiftMonth(-1)}>
            <ChevronLeft aria-hidden="true" size={14} />
          </button>
          <button aria-label="下个月" className="ops-icon-button" type="button" onClick={() => shiftMonth(1)}>
            <ChevronRight aria-hidden="true" size={14} />
          </button>
          <span className="ops-calendar-mode">
            {mode === 'loading' && '加载中…'}
            {mode === 'sample' && '示例数据 · 订阅日历未连接'}
            {mode === 'live' && `${entries.length} 条播出记录`}
            {mode === 'error' && '订阅引擎不可用'}
          </span>
          </div>
        </header>
        {calendarErrors.length > 0 && <p className="ops-calendar-error">{calendarErrors[0]}</p>}
        {mode === 'error' && <p className="ops-calendar-error">订阅服务当前不可用，没有显示旧记录或示例数据。</p>}
        <div className="ops-calendar-scroll">
        <div aria-label={`${year} 年 ${month} 月订阅日历`} className="calendar-grid" role="grid">
          {weekdays.map((day) => (
            <div className="calendar-grid__weekday" key={day} role="columnheader">
              {day}
            </div>
          ))}
          {cells.map((day, index) => {
            if (day === null) {
              return <div aria-hidden="true" className="calendar-cell calendar-cell--empty" key={`empty-${index}`} />;
            }
            const dateKey = toDateKey(year, month, day);
            const dayEntries = entriesByDay.get(day) ?? [];
            return (
              <div
                className={dateKey === todayKey ? 'calendar-cell calendar-cell--today' : 'calendar-cell'}
                key={day}
                role="gridcell"
              >
                <span className="calendar-cell__date">{day}</span>
                {dayEntries.map((entry) => {
                  const state = entryState(entry, todayKey);
                  return state === '未入库' ? (
                    <button
                      className={entryClass[state]}
                      key={`${entry.title}-${entry.episodeLabel}`}
                      title={`${entry.title} ${entry.episodeLabel} · 已播出但未入库，点击去任务中心`}
                      type="button"
                      onClick={() => onNavigate('tasks')}
                    >
                      <EntryPoster entry={entry} />
                      <span className="calendar-entry__text">
                        <strong>{entry.title}</strong>
                        <small>{entry.episodeLabel} · 未入库</small>
                      </span>
                    </button>
                  ) : (
                    <div
                      className={entryClass[state]}
                      key={`${entry.title}-${entry.episodeLabel}`}
                      title={`${entry.title} ${entry.episodeLabel}`}
                    >
                      <EntryPoster entry={entry} />
                      <span className="calendar-entry__text">
                        <strong>
                          {state === '已入库' && <Check aria-hidden="true" size={11} />}
                          {entry.title}
                        </strong>
                        <small>{entry.episodeLabel}</small>
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
        </div>
        <footer className="ops-calendar-legend">
          <span><i className="is-upcoming" />待播出</span>
          <span><i className="is-library" />已入库</span>
          <span><i className="is-overdue" />逾期未入库，可点击排查</span>
          <strong>默认获取通道：PT</strong>
        </footer>
      </section>
    </main>
  );
}
