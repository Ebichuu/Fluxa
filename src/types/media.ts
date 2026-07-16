export type MediaKind = 'Movie' | 'Series' | 'Episode' | 'Video';

export interface MediaItem {
  id: string;
  title: string;
  year: string;
  type: MediaKind;
  genres: string[];
  rating: string;
  posterUrl: string;
  backdropUrl: string;
  overview?: string;
  libraryId?: string;
  libraryName?: string;
  sourceName?: string;
}

export interface MediaLibrary {
  id: string;
  name: string;
  collectionType: string;
  posterUrl: string;
  backdropUrl: string;
  itemCount?: number;
}

export interface HomeMediaResponse {
  items: MediaItem[];
  libraries: MediaLibrary[];
  activeLibraryId?: string;
  source: 'emby' | 'sample';
  configured: boolean;
  error?: string;
}

export interface HealthResponse {
  app: string;
  status: 'ok';
  services: Array<{
    id: string;
    name: string;
    type: string;
    configured: boolean;
  }>;
}
