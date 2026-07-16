export interface SymediaTransferItem {
  title: string;
  year: string;
  mediaType: string;
  seasonEpisode: string;
  mode: string;
  status: boolean;
  errmsg: string;
  date: string;
}

export interface SymediaSummary {
  configured: boolean;
  connected: boolean;
  webUrl: string;
  lastCheckedAt: string;
  totals: {
    records: number;
    today: number;
    failedRecent: number;
  };
  latest: SymediaTransferItem[];
  error?: string;
}
