import type { PageId, TaskNavigationTarget } from '../components/layout/AppTopNav';

const canonicalRoutes: Record<PageId, string> = {
  overview: '/',
  hall: '/hall',
  control: '/control',
  tasks: '/tasks',
  calendar: '/calendar',
  discover: '/discover',
  subscriptions: '/following',
  'subscription-settings': '/following/settings',
  'rss-library': '/rss-library',
  settings: '/settings'
};

const legacyRoutes: Record<string, PageId> = {
  '/overview': 'overview',
  '/subscriptions': 'subscriptions',
  '/subscription-settings': 'subscription-settings',
  '/tasks-center': 'tasks',
  '/control-room': 'control'
};

export interface NavigationState {
  page: PageId;
  target: TaskNavigationTarget | null;
  canonical: boolean;
  search: string;
}

function optionalString(value: string | null) {
  const normalized = value?.trim();
  return normalized ? normalized : undefined;
}

export function readNavigation(location: Location = window.location): NavigationState {
  const pathname = location.pathname.replace(/\/+$/, '') || '/';
  const page = (Object.entries(canonicalRoutes).find(([, route]) => route === pathname)?.[0] as PageId | undefined)
    ?? legacyRoutes[pathname]
    ?? 'overview';
  const query = new URLSearchParams(location.search);
  const season = Number(query.get('seasonNumber'));
  const target: TaskNavigationTarget | null = ['tasks', 'subscriptions'].includes(page) && (
    query.has('chainId') || query.has('targetKey') || query.has('subscriptionId') || query.has('tmdbId') || query.has('title')
  ) ? {
    mediaType: query.get('mediaType') === 'movie' ? 'movie' : query.get('mediaType') === 'tv' ? 'tv' : undefined,
    chainId: optionalString(query.get('chainId')),
    targetKey: optionalString(query.get('targetKey')),
    subscriptionId: optionalString(query.get('subscriptionId')),
    tmdbId: optionalString(query.get('tmdbId')),
    title: optionalString(query.get('title')),
    seasonNumber: Number.isFinite(season) && season > 0 ? season : undefined
  } : null;

  return {
    page,
    target,
    canonical: canonicalRoutes[page] === pathname,
    search: location.search
  };
}

export function pathForNavigation(page: PageId, target?: TaskNavigationTarget | null) {
  const route = canonicalRoutes[page];
  const query = new URLSearchParams();
  if (['tasks', 'subscriptions'].includes(page) && target) {
    if (target.mediaType) query.set('mediaType', target.mediaType);
    if (target.chainId) query.set('chainId', target.chainId);
    if (target.targetKey) query.set('targetKey', target.targetKey);
    if (target.subscriptionId) query.set('subscriptionId', target.subscriptionId);
    if (target.tmdbId) query.set('tmdbId', target.tmdbId);
    if (target.title) query.set('title', target.title);
    if (target.seasonNumber != null) query.set('seasonNumber', String(target.seasonNumber));
  }
  const search = query.toString();
  return search ? `${route}?${search}` : route;
}
