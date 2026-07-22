import { CheckCircle2, CircleHelp, Clock3, ShieldCheck, TriangleAlert } from 'lucide-react';

export type HealthStateValue = 'normal' | 'waiting' | 'protected' | 'evidence_insufficient' | 'action_required';

const healthConfig = {
  normal: { label: '正常', icon: CheckCircle2 },
  waiting: { label: '等待', icon: Clock3 },
  protected: { label: '正常保护', icon: ShieldCheck },
  evidence_insufficient: { label: '证据不足', icon: CircleHelp },
  action_required: { label: '需要处理', icon: TriangleAlert }
} satisfies Record<HealthStateValue, { label: string; icon: typeof CheckCircle2 }>;

export function healthStatusLabel(state: HealthStateValue) {
  return healthConfig[state].label;
}

export function HealthBadge({
  className = '',
  label,
  state
}: {
  className?: string;
  label?: string;
  state: HealthStateValue;
}) {
  const Icon = healthConfig[state].icon;
  return (
    <span className={`ops-health-badge ops-health-badge--${state}${className ? ` ${className}` : ''}`} data-health={state}>
      <Icon aria-hidden="true" size={12} strokeWidth={2} />
      <span>{label || healthConfig[state].label}</span>
    </span>
  );
}
