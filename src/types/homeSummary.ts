export type HealthState = 'normal' | 'protected' | 'waiting' | 'evidence_insufficient' | 'action_required';

export interface HomeSummaryCounts {
  ingestedToday: number;
  downloading: number;
  pending: number;
  waiting: number;
  evidenceInsufficient: number;
  actionRequired: number;
  protected: number;
}

export interface HomeSummaryIssue {
  headline?: string;
  healthState: HealthState;
  observedAt: string;
  freshUntil: string;
  source: string;
  reasonCode: string;
  reasonText: string;
  targetKey: string;
  chainId: string;
  title: string;
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
