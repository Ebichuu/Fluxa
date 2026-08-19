import type { AutomationAction, RssMatch, RssMatchGroup, RssSeedItem } from '../types/rssSeedLibrary';

type RssOwnership = NonNullable<RssMatchGroup['ownerships']>[number];

export interface RssOwnershipSummary {
  key: string;
  state: RssOwnership['state'];
  label: string;
  count: number;
  subscriptionId: string;
  reasonCode: string;
}

export function rssResourceClassificationBlocker(
  item: Pick<RssSeedItem, 'identityStatus' | 'mediaType'>
): { title: string; actionLabel: string } | null {
  if (item.identityStatus === 'conflict') {
    return {
      title: '媒体身份存在冲突，暂不能自动分类',
      actionLabel: '身份冲突'
    };
  }
  if (item.identityStatus !== 'identified') {
    return {
      title: '媒体身份未确认，暂不能自动分类',
      actionLabel: '需先识别'
    };
  }
  if (!['movie', 'tv'].includes(item.mediaType)) {
    return {
      title: '媒体类型未确认，暂不能自动分类',
      actionLabel: '需确认类型'
    };
  }
  return null;
}

function ownershipLabel(ownership: RssOwnership) {
  if (ownership.state === 'invalid' && ownership.reasonCode === 'subscription_missing') {
    return '原 Torra 订阅已删除';
  }
  if (ownership.state === 'invalid') return '原订阅归属已失效';
  if (ownership.state === 'archived') return '候选归属已归档';
  if (ownership.state === 'conflict') return '候选归属需要确认';
  return '有效订阅归属';
}

export function summarizeRssOwnerships(ownerships: RssOwnership[]): RssOwnershipSummary[] {
  const summaries = new Map<string, RssOwnershipSummary>();
  ownerships.forEach((ownership) => {
    const reasonCode = ownership.reasonCode || '';
    const key = [ownership.state, reasonCode, ownership.subscriptionId].join(':');
    const current = summaries.get(key);
    if (current) {
      current.count += 1;
      return;
    }
    summaries.set(key, {
      key,
      state: ownership.state,
      label: ownershipLabel(ownership),
      count: 1,
      subscriptionId: ownership.subscriptionId,
      reasonCode
    });
  });
  return Array.from(summaries.values());
}

export function rssCandidateGroupScope(
  view: 'new' | 'identify' | 'scoring' | 'upgrades' | 'cleanup',
  hasScopedContext: boolean
): 'scoreable' | 'decision' | undefined {
  if (hasScopedContext) return undefined;
  return view === 'cleanup' ? 'decision' : 'scoreable';
}

export interface RssTorraAnalysisPresentation {
  enabled: boolean;
  label: string;
  title: string;
  primary: boolean;
}

const retryableAnalysisReasons = new Set([
  'torra_unavailable',
  'torra_rule_read_failed'
]);

export function rssTorraAnalysisPresentation(
  match: Pick<RssMatch, 'status' | 'evaluationStatus' | 'evaluationReason'>,
  action?: Pick<AutomationAction, 'status'>
): RssTorraAnalysisPresentation {
  if (action && !['succeeded', 'failed', 'cancelled'].includes(action.status)) {
    return {
      enabled: false,
      label: '整条订阅分析中',
      title: 'Torra 正在分析整条订阅',
      primary: false
    };
  }
  if (action?.status === 'failed' || action?.status === 'cancelled') {
    return {
      enabled: true,
      label: '重试整条订阅分析',
      title: '重新触发 Torra 对整条订阅执行搜索分析',
      primary: true
    };
  }
  if (action?.status === 'succeeded') {
    return {
      enabled: false,
      label: '整条订阅已分析',
      title: 'Torra 已完成整条订阅分析',
      primary: false
    };
  }
  if (match.evaluationStatus === 'blocked') {
    const retryable = retryableAnalysisReasons.has(match.evaluationReason || '');
    return retryable
      ? {
          enabled: true,
          label: '重试整条订阅分析',
          title: 'Torra 暂时不可用，可重新分析整条订阅',
          primary: true
        }
      : {
          enabled: false,
          label: '需先解决阻断',
          title: '当前阻断无法通过重新分析订阅解决',
          primary: false
        };
  }
  if (match.evaluationStatus === 'scored') {
    return {
      enabled: false,
      label: '评分已完成',
      title: '当前候选已完成评分，无需重新分析整条订阅',
      primary: false
    };
  }
  const enabled = match.status === 'candidate';
  return {
    enabled,
    label: enabled ? '分析整条订阅' : '无需处理',
    title: enabled
      ? '触发 Torra 对整条订阅执行搜索分析'
      : '当前候选无需人工分析',
    primary: enabled
  };
}

export function rssCandidateScoreGain(
  group: Pick<RssMatchGroup, 'bestCandidateScore' | 'baselineScore'>
): number | null {
  if (typeof group.bestCandidateScore !== 'number' || typeof group.baselineScore !== 'number') {
    return null;
  }
  return group.bestCandidateScore - group.baselineScore;
}

export function rssMinorUpgradeWarning(
  group: Pick<RssMatchGroup, 'state' | 'bestCandidateScore' | 'baselineScore'>
): string {
  const gain = rssCandidateScoreGain(group);
  if (group.state !== 'upgrade_available' || gain == null || gain <= 0 || gain >= 1) return '';
  const formatted = Number.isInteger(gain) ? String(gain) : gain.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return `仅提升 ${formatted} 分，属于小幅提升；建议先核对版本差异。`;
}
