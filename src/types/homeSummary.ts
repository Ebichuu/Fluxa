export type HealthState = 'normal' | 'protected' | 'waiting' | 'evidence_insufficient' | 'action_required';

export interface HomeSummaryCounts {
  ingestedToday: number;
  archivedToday: number | null;
  completedTargetsToday: number;
  downloading: number;
  activeDownloadTasks: number | null;
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

export type HomeSummaryFocusKey = 'current_downloads' | 'secupload_failures' | 'downloaded_not_archived' | 'archived_today' | 'missing_episodes' | 'action_required';
export type HomeSummaryFocusState = 'normal' | 'processing' | 'action_required' | 'unknown';

export interface HomeSummaryFocusItem {
  key: HomeSummaryFocusKey;
  label: string;
  unit: string;
  value: number | null;
  state: HomeSummaryFocusState;
  detail: string;
  href: string;
}

export interface HomeSummaryResponse {
  ok: boolean;
  generatedAt: string;
  healthState: HealthState;
  headline: string;
  detail: string;
  counts: HomeSummaryCounts;
  focusItems: HomeSummaryFocusItem[];
  issueTotal?: number;
  issues: HomeSummaryIssue[];
  diagnosticTotal?: number;
  diagnostics?: Array<{
    code: string;
    count: number;
    label: string;
    reasonText?: string;
    source?: string;
    href?: string;
  }>;
}
