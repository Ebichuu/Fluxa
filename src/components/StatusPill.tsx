import type { StepStatus } from '../types/api';

interface StatusPillProps {
  status: StepStatus | string;
  label: string;
}

export function StatusPill({ status, label }: StatusPillProps) {
  const tone =
    status === 'ok' || status === 'in_emby' || status === 'site_official'
      ? 'ok'
      : status === 'active' || status === 'processing'
        ? 'active'
        : status === 'warn'
          ? 'warn'
          : status === 'error' || status === 'issue' || status === 'cross_site_repost'
            ? 'error'
            : status === 'protected'
              ? 'protected'
              : 'unconfirmed';
  return <span className={`pill ${tone}`}>{label}</span>;
}
