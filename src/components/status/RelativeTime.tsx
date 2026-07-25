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

export function RelativeTime({ value, fallback = '时间未知', className, interactive = true }: RelativeTimeProps) {
  const [showExact, setShowExact] = useState(false);
  const exact = value ? exactShanghaiTime(value) : '';
  if (!value || !exact) return <span className={className}>{fallback}</span>;
  const relative = formatTimeAgo(value);
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
