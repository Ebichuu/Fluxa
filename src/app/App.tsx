import { lazy, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AppTopNav, type AppNavigate, type PageId, type TaskNavigationTarget, type ThemeMode } from '../components/layout/AppTopNav';
import { LazyRouteBoundary } from '../components/layout/LazyRouteBoundary';
import { Overview } from '../components/pages/Overview';
import { usePolling } from '../hooks/usePolling';
import { getHomeSummary } from '../services/api';
import type { HomeSummaryResponse } from '../types/homeSummary';
import { defaultVisualFx, normalizeVisualFx } from '../types/visualFx';
import { readLocalStorage, writeLocalStorage } from '../utils/storage';
import { pathForNavigation, readNavigation } from './navigation';
import { initializeHistoryEntry, saveCurrentScrollPosition, scrollPositionFromHistoryState, writePath } from './urlState';

const VISUAL_FX_VERSION = '4';
const THEME_STORAGE_KEY = 'mcc-ui-theme';

const MediaHall = lazy(() => import('../components/media-hall/MediaHall').then((module) => ({ default: module.MediaHall })));
const CalendarPage = lazy(() => import('../components/pages/CalendarPage').then((module) => ({ default: module.CalendarPage })));
const ControlRoom = lazy(() => import('../components/pages/ControlRoom').then((module) => ({ default: module.ControlRoom })));
const DiscoverPage = lazy(() => import('../components/pages/DiscoverPage').then((module) => ({ default: module.DiscoverPage })));
const MediaOverviewPage = lazy(() => import('../components/pages/MediaOverviewPage').then((module) => ({ default: module.MediaOverviewPage })));
const SettingsPage = lazy(() => import('../components/pages/SettingsPage').then((module) => ({ default: module.SettingsPage })));
const SubscriptionSettingsPage = lazy(() => import('../components/pages/SubscriptionSettingsPage').then((module) => ({ default: module.SubscriptionSettingsPage })));
const TasksCenter = lazy(() => import('../components/pages/TasksCenter').then((module) => ({ default: module.TasksCenter })));
const RssSeedLibraryPage = lazy(() => import('../components/pages/RssSeedLibraryPage').then((module) => ({ default: module.RssSeedLibraryPage })));

function initialTheme(): ThemeMode {
  const saved = readLocalStorage(THEME_STORAGE_KEY);
  if (saved === 'dark' || saved === 'light') return saved;

  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function App() {
  const [navigation] = useState(readNavigation);
  const [page, setPage] = useState<PageId>(navigation.page);
  const [navigationTarget, setNavigationTarget] = useState<TaskNavigationTarget | null>(navigation.target);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [theme, setTheme] = useState<ThemeMode>(initialTheme);
  const [homeSummary, setHomeSummary] = useState<HomeSummaryResponse | null>(null);
  const [visualFx, setVisualFx] = useState(() => {
    try {
      const saved = readLocalStorage('hallVisualFx');
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<typeof defaultVisualFx>;
        const shouldMigrateDefaults = readLocalStorage('hallVisualFxVersion') !== VISUAL_FX_VERSION;
        return normalizeVisualFx({
          ...parsed,
          point: parsed.point == null || parsed.point === 1 ? defaultVisualFx.point : parsed.point,
          shelfSize:
            parsed.shelfSize == null || (shouldMigrateDefaults && parsed.shelfSize === 1)
              ? defaultVisualFx.shelfSize
              : parsed.shelfSize,
          shelfOffsetX:
            parsed.shelfOffsetX == null ||
            (shouldMigrateDefaults &&
              (parsed.shelfOffsetX === 0 || parsed.shelfOffsetX === -0.22 || parsed.shelfOffsetX === -0.58))
              ? defaultVisualFx.shelfOffsetX
              : parsed.shelfOffsetX
        });
      }
    } catch {
      // Ignore old or malformed local visual settings.
    }

    const legacyPreset = Number(readLocalStorage('hallVisualPreset'));
    return normalizeVisualFx({
      ...defaultVisualFx,
      preset: Number.isFinite(legacyPreset) ? legacyPreset : defaultVisualFx.preset
    });
  });
  const pendingScrollRef = useRef<number | null>(0);

  const loadHomeSummary = async (signal: AbortSignal) => {
    try {
      const summary = await getHomeSummary({ signal });
      if (!signal.aborted) setHomeSummary(summary);
    } catch {
      if (!signal.aborted) setHomeSummary(null);
    }
  };

  usePolling(loadHomeSummary, 30_000);

  useEffect(() => {
    writeLocalStorage('hallVisualFx', JSON.stringify(visualFx));
    writeLocalStorage('hallVisualFxVersion', VISUAL_FX_VERSION);
    writeLocalStorage('hallVisualPreset', String(visualFx.preset));
  }, [visualFx]);

  useEffect(() => {
    writeLocalStorage(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    const root = document.documentElement;
    if (page === 'hall') {
      delete root.dataset.workbenchTheme;
    } else {
      root.dataset.workbenchTheme = theme;
    }

    return () => {
      delete root.dataset.workbenchTheme;
    };
  }, [page, theme]);

  useEffect(() => {
    initializeHistoryEntry();
    const previousRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = 'manual';
    let frame = 0;
    const handleScroll = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(saveCurrentScrollPosition);
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      window.cancelAnimationFrame(frame);
      saveCurrentScrollPosition();
      window.removeEventListener('scroll', handleScroll);
      window.history.scrollRestoration = previousRestoration;
    };
  }, []);

  useLayoutEffect(() => {
    const targetScroll = pendingScrollRef.current ?? 0;
    pendingScrollRef.current = null;
    let cancelled = false;
    let attempts = 0;
    let timer = 0;

    const restore = () => {
      if (cancelled) return;
      window.scrollTo({ top: targetScroll, left: 0, behavior: 'auto' });
      const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      if (targetScroll > maxScroll && attempts < 12) {
        attempts += 1;
        timer = window.setTimeout(restore, 100);
      }
    };

    const frame = window.requestAnimationFrame(restore);
    const cancelRestore = () => {
      cancelled = true;
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
    window.addEventListener('pointerdown', cancelRestore, { once: true });
    window.addEventListener('touchstart', cancelRestore, { once: true, passive: true });
    window.addEventListener('wheel', cancelRestore, { once: true, passive: true });
    return () => {
      cancelRestore();
      window.removeEventListener('pointerdown', cancelRestore);
      window.removeEventListener('touchstart', cancelRestore);
      window.removeEventListener('wheel', cancelRestore);
    };
  }, [historyRevision, page]);

  useEffect(() => {
    const handlePopState = (event: PopStateEvent) => {
      const next = readNavigation();
      pendingScrollRef.current = scrollPositionFromHistoryState(event.state) ?? 0;
      setPage(next.page);
      setNavigationTarget(['tasks', 'subscriptions', 'rss-library', 'media'].includes(next.page) ? next.target : null);
      setHistoryRevision((current) => current + 1);
    };
    window.addEventListener('popstate', handlePopState);
    if (!navigation.canonical) {
      const canonicalPath = pathForNavigation(navigation.page, navigation.target).split('?')[0];
      writePath(`${canonicalPath}${navigation.search}`, 'replace');
    }
    return () => window.removeEventListener('popstate', handlePopState);
  }, [navigation]);

  const navigate: AppNavigate = (nextPage, target) => {
    pendingScrollRef.current = 0;
    setPage(nextPage);
    const nextTarget = ['tasks', 'subscriptions', 'rss-library', 'media'].includes(nextPage) ? target ?? null : null;
    setNavigationTarget(nextTarget);
    writePath(pathForNavigation(nextPage, nextTarget), 'push');
    setHistoryRevision((current) => current + 1);
  };

  const navigatePath = (path: string) => {
    const url = new URL(path, window.location.href);
    if (url.origin !== window.location.origin) {
      window.location.assign(url.href);
      return;
    }
    pendingScrollRef.current = 0;
    writePath(`${url.pathname}${url.search}${url.hash}`, 'push');
    const next = readNavigation();
    setPage(next.page);
    setNavigationTarget(['tasks', 'subscriptions', 'rss-library', 'media'].includes(next.page) ? next.target : null);
    setHistoryRevision((current) => current + 1);
  };

  return (
    <div className={`app-shell app-shell--${page}`} data-theme={page === 'hall' ? undefined : theme}>
      <AppTopNav
        activePage={page}
        homeSummary={homeSummary}
        onNavigate={navigate}
        onToggleTheme={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
        showThemeToggle={page !== 'hall'}
        theme={theme}
      />
      {page === 'overview' && <Overview onNavigate={navigate} onNavigatePath={navigatePath} />}
      {page !== 'overview' && (
        <LazyRouteBoundary routeKey={page}>
          {page === 'media' && <MediaOverviewPage target={navigationTarget} onNavigate={navigate} onNavigatePath={navigatePath} />}
          {page === 'hall' && (
            <MediaHall
              visualFx={visualFx}
              onVisualFxChange={(nextVisualFx) =>
                setVisualFx((currentVisualFx) => normalizeVisualFx({ ...currentVisualFx, ...nextVisualFx }))
              }
            />
          )}
          {page === 'control' && <ControlRoom />}
          {page === 'tasks' && <TasksCenter target={navigationTarget} onClearTarget={() => setNavigationTarget(null)} onNavigate={navigate} />}
          {page === 'calendar' && <CalendarPage onNavigate={navigate} />}
          {(page === 'discover' || page === 'subscriptions') && (
            <DiscoverPage
              navigationTarget={page === 'subscriptions' ? navigationTarget : null}
              onNavigate={navigate}
              view={page === 'subscriptions' ? 'subscriptions' : 'discover'}
            />
          )}
          {page === 'subscription-settings' && <SubscriptionSettingsPage onNavigate={navigate} />}
          {page === 'rss-library' && <RssSeedLibraryPage onNavigate={navigate} />}
          {page === 'settings' && <SettingsPage />}
        </LazyRouteBoundary>
      )}
    </div>
  );
}
