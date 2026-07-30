export type StatisticConfirmation = 'confirmed' | 'partial' | 'unknown';

export interface StatisticMetadata {
  scope: string;
  unit: string;
  observedAt: string;
  confirmation: StatisticConfirmation;
}
