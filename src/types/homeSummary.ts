export type HealthState = 'normal' | 'protected' | 'waiting' | 'evidence_insufficient' | 'action_required';

export interface HomeSummaryCounts {
  ingestedToday: number;
  archivedToday: number;
  completedTargetsToday: number;
  downloading: number;
  activeDownloadTasks: number;
  concurrentDownloadGroups: number;
  pending: number;
  waiting: number;
  evidenceInsufficient: number;
  identityPending: number;
  actionRequired: number;
  suspectedBlocked: number;
  protected: number;
}

export interface HomeSummaryIssue {
  headline?: string;
  displayTitle?: string;
  healthState: HealthState;
  observedAt: string;
  freshUntil: string;
  source: string;
  reasonCode: string;
  reasonText: string;
  targetKey: string;
  chainId: string;
  title: string;
  seasonNumber?: number;
  episodeNumber?: number;
  secondaryReasonText?: string;
  identityState?: 'unidentified' | 'linked' | 'conflict';
  executionState?: 'normal' | 'waiting' | 'protected' | 'suspected_blocked' | 'action_required' | 'confirmed_failed';
}

export interface HomeSummaryResponse {
  ok: boolean;
  generatedAt: string;
  healthState: HealthState;
  headline: string;
  detail: string;
  counts: HomeSummaryCounts;
  issues: HomeSummaryIssue[];
}
