export type UrlHistoryMode = 'push' | 'replace';

type QueryValue = string | number | boolean | Array<string | number> | null | undefined;

const sessionId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const sessionKey = '__fluxaNavigationSession';
const scrollKey = '__fluxaScrollY';
const entryKindKey = '__fluxaEntryKind';

function stateRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function sessionState(scrollY = window.scrollY, entryKind?: string) {
  return {
    [sessionKey]: sessionId,
    [scrollKey]: Math.max(0, Math.round(scrollY)),
    ...(entryKind ? { [entryKindKey]: entryKind } : {})
  };
}

export function initializeHistoryEntry() {
  const current = stateRecord(window.history.state);
  if (current[sessionKey] === sessionId) return;
  window.history.replaceState({ ...current, ...sessionState(0), [entryKindKey]: undefined }, '', window.location.href);
}

export function saveCurrentScrollPosition() {
  const current = stateRecord(window.history.state);
  window.history.replaceState({ ...current, ...sessionState() }, '', window.location.href);
}

export function scrollPositionFromHistoryState(value: unknown) {
  const state = stateRecord(value);
  if (state[sessionKey] !== sessionId) return null;
  const scrollY = Number(state[scrollKey]);
  return Number.isFinite(scrollY) && scrollY >= 0 ? scrollY : 0;
}

export function currentHistoryEntryIs(kind: string) {
  const state = stateRecord(window.history.state);
  return state[sessionKey] === sessionId && state[entryKindKey] === kind;
}

export function writePath(path: string, mode: UrlHistoryMode, entryKind?: string) {
  if (mode === 'push') {
    saveCurrentScrollPosition();
    window.history.pushState(sessionState(window.scrollY, entryKind), '', path);
    return;
  }

  const current = stateRecord(window.history.state);
  window.history.replaceState({
    ...current,
    ...sessionState(window.scrollY, entryKind ?? (current[entryKindKey] as string | undefined))
  }, '', path);
}

export function writeUrlQuery(patch: Record<string, QueryValue>, mode: UrlHistoryMode, entryKind?: string) {
  const url = new URL(window.location.href);
  Object.entries(patch).forEach(([key, value]) => {
    url.searchParams.delete(key);
    if (value == null || value === false || value === '') return;
    const values = Array.isArray(value) ? value : [value === true ? 1 : value];
    values.forEach((item) => url.searchParams.append(key, String(item)));
  });
  writePath(`${url.pathname}${url.search}${url.hash}`, mode, entryKind);
}
