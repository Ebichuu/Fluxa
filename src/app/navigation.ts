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
  settings: '/settings',
  media: '/media'
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

type NavigationOutcomeState = NonNullable<TaskNavigationTarget['outcomeState']>;

const validOutcomeStates: NavigationOutcomeState[] = [
  'waiting', 'in_progress', 'protected', 'action_required', 'playable', 'evidence_insufficient'
];

function isOutcomeState(value: string): value is NavigationOutcomeState {
  return validOutcomeStates.includes(value as NavigationOutcomeState);
}

function legacyOutcomes(value: string | undefined): NavigationOutcomeState[] {
  if (value === 'action_required') return ['action_required'];
  if (value === 'in_progress') return ['in_progress'];
  if (value === 'completed') return ['playable'];
  if (value === 'no_action') return ['waiting', 'protected', 'evidence_insufficient'];
  return [];
}

export function readNavigation(location: Location = window.location): NavigationState {
  const pathname = location.pathname.replace(/\/+$/, '') || '/';
  const mediaRoute = pathname.match(/^\/media\/(movie|tv)\/(\d+)$/);
  const page = (mediaRoute ? 'media' : Object.entries(canonicalRoutes).find(([, route]) => route === pathname)?.[0] as PageId | undefined)
    ?? legacyRoutes[pathname]
    ?? 'overview';
  const query = new URLSearchParams(location.search);
  const season = Number(query.get('seasonNumber'));
  const explicitOutcomeStates = query.getAll('outcomeState').filter(isOutcomeState);
  const outcomeStates = [...new Set(
    explicitOutcomeStates.length
      ? explicitOutcomeStates
      : legacyOutcomes(optionalString(query.get('userState')))
  )];
  const target: TaskNavigationTarget | null = page === 'media' && mediaRoute ? {
    mediaType: mediaRoute[1] as 'movie' | 'tv',
    tmdbId: mediaRoute[2]
  } : ['tasks', 'subscriptions'].includes(page) && (
    query.has('chainId') || query.has('targetKey') || query.has('subscriptionId') || query.has('tmdbId') || query.has('title')
    || query.has('outcomeState') || query.has('userState') || query.has('completedDate') || query.has('advanced') || query.has('identityState')
    || query.has('systemIssue')
  ) ? {
    mediaType: query.get('mediaType') === 'movie' ? 'movie' : query.get('mediaType') === 'tv' ? 'tv' : undefined,
    chainId: optionalString(query.get('chainId')),
    targetKey: optionalString(query.get('targetKey')),
    subscriptionId: optionalString(query.get('subscriptionId')),
    tmdbId: optionalString(query.get('tmdbId')),
    title: optionalString(query.get('title')),
    seasonNumber: Number.isFinite(season) && season > 0 ? season : undefined,
    outcomeState: outcomeStates[0],
    outcomeStates,
    userState: ['action_required', 'in_progress', 'completed', 'no_action'].includes(query.get('userState') || '')
      ? query.get('userState') as TaskNavigationTarget['userState']
      : undefined,
    completedDate: optionalString(query.get('completedDate')),
    advanced: query.get('advanced') === '1',
    identityStates: query.getAll('identityState').filter((value): value is 'unidentified' | 'linked' | 'conflict' => (
      ['unidentified', 'linked', 'conflict'].includes(value)
    )),
    systemIssue: optionalString(query.get('systemIssue'))
  } : null;

  return {
    page,
    target,
    canonical: page === 'media' ? Boolean(mediaRoute) : canonicalRoutes[page] === pathname,
    search: location.search
  };
}

export function pathForNavigation(page: PageId, target?: TaskNavigationTarget | null) {
  if (page === 'media' && target?.mediaType && target.tmdbId && /^\d+$/.test(target.tmdbId)) {
    return `/media/${target.mediaType}/${target.tmdbId}`;
  }
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
    const requestedOutcomeStates = target.outcomeStates?.length
      ? target.outcomeStates
      : target.outcomeState
        ? [target.outcomeState]
        : legacyOutcomes(target.userState);
    const outcomeStates = [...new Set(requestedOutcomeStates)];
    outcomeStates.forEach((value) => query.append('outcomeState', value));
    if (target.completedDate) query.set('completedDate', target.completedDate);
    if (target.advanced) query.set('advanced', '1');
    target.identityStates?.forEach((value) => query.append('identityState', value));
    if (target.systemIssue) query.set('systemIssue', target.systemIssue);
  }
  const search = query.toString();
  return search ? `${route}?${search}` : route;
}
