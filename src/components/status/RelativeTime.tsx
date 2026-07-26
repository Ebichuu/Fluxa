import { useState, type KeyboardEvent } from 'react';
import { formatTimeAgo } from '../../utils/formatters';

interface RelativeTimeProps {
  value?: string | null;
  fallback?: string;
  className?: string;
  interactive?: boolean;
}

function exactShanghaiTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(date);
}

// 超过该阈值的未来时间才按“未来时刻”展示，避免服务端轻微时钟偏差误判
const FUTURE_THRESHOLD_MS = 60_000;

function shanghaiDateStamp(date: Date) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(date);
}

function futureShanghaiLabel(date: Date, now: Date) {
  const clock = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date);
  const targetDay = shanghaiDateStamp(date);
  const todayDay = shanghaiDateStamp(now);
  const tomorrowDay = shanghaiDateStamp(new Date(now.getTime() + 86_400_000));
  const dayLabel = targetDay === todayDay
    ? '今天'
    : targetDay === tomorrowDay
      ? '明天'
      : `${Number(targetDay.slice(5, 7))}月${Number(targetDay.slice(8, 10))}日`;
  const minutes = Math.round((date.getTime() - now.getTime()) / 60_000);
  const relative = minutes < 60
    ? `约 ${Math.max(1, minutes)} 分钟后`
    : minutes < 1440
      ? `约 ${Math.round(minutes / 60)} 小时后`
      : `约 ${Math.round(minutes / 1440)} 天后`;
  return `${dayLabel}${clock}（${relative}）`;
}

export function RelativeTime({ value, fallback = '时间未知', className, interactive = true }: RelativeTimeProps) {
  const [showExact, setShowExact] = useState(false);
  const exact = value ? exactShanghaiTime(value) : '';
  if (!value || !exact) return <span className={className}>{fallback}</span>;
  // 自动检测未来时间：超过阈值时显示“今天18:00（约25分钟后）”风格，过去时间行为不变
  const parsed = new Date(value);
  const now = new Date();
  const isFuture = parsed.getTime() - now.getTime() > FUTURE_THRESHOLD_MS;
  const relative = isFuture ? futureShanghaiLabel(parsed, now) : formatTimeAgo(value);
  const timeClassName = className ? `relative-time ${className}` : 'relative-time';
  if (!interactive) {
    return <time className={timeClassName} dateTime={value} title={`北京时间 ${exact}`}>{relative}</time>;
  }
  const toggle = () => setShowExact((current) => !current);
  const handleKeyDown = (event: KeyboardEvent<HTMLTimeElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggle();
  };
  return (
    <time
      aria-label={`${relative}，北京时间 ${exact}，点击切换显示`}
      className={timeClassName}
      dateTime={value}
      role="button"
      tabIndex={0}
      title={`北京时间 ${exact}`}
      onClick={toggle}
      onKeyDown={handleKeyDown}
    >
      {showExact ? exact : relative}
    </time>
  );
}
