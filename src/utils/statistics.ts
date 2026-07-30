import type { StatisticMetadata } from '../types/statistics';

const scopeLabels: Record<string, string> = {
  home_today: '今日媒体结果',
  current_qb_snapshot: '当前 qB 快照',
  current_unique_task_chains: '当前唯一任务链',
  current_subscription_ledger: '当前追更台账',
  calendar_query: '当前日历查询'
};

const confirmationLabels: Record<StatisticMetadata['confirmation'], string> = {
  confirmed: '已确认',
  partial: '部分确认',
  unknown: '当前未知'
};

export function statisticScopeText(meta: StatisticMetadata | undefined, fallback: string) {
  if (!meta) return fallback;
  return `${scopeLabels[meta.scope] ?? fallback} · ${confirmationLabels[meta.confirmation]}`;
}

export function statisticDisplayValue(value: number, meta?: StatisticMetadata) {
  return meta?.confirmation === 'unknown' ? '未知' : value;
}
