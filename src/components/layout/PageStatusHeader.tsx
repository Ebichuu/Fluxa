import type { ReactNode } from 'react';

interface PageStatusHeaderProps {
  title: string;
  context?: string;
  status: ReactNode;
  detail?: ReactNode;
  tone?: 'ok' | 'warn' | 'neutral';
  actions?: ReactNode;
}

export function PageStatusHeader({
  title,
  context,
  status,
  detail,
  tone = 'neutral',
  actions
}: PageStatusHeaderProps) {
  return (
    <header className={`page-status-header page-status-header--${tone}`}>
      <div className="page-status-header__identity">
        {context && <span>{context}</span>}
        <h1>{title}</h1>
      </div>
      <div aria-live="polite" className="page-status-header__state">
        <strong>{status}</strong>
        {detail && <span>{detail}</span>}
      </div>
      {actions && <div className="page-status-header__actions">{actions}</div>}
    </header>
  );
}
