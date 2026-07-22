import { useEffect, useState } from 'react';
import { AppTopNav, type AppNavigate, type PageId, type TaskNavigationTarget, type ThemeMode } from '../components/layout/AppTopNav';
import { MediaHall } from '../components/media-hall/MediaHall';
import { CalendarPage } from '../components/pages/CalendarPage';
import { ControlRoom } from '../components/pages/ControlRoom';
import { DiscoverPage } from '../components/pages/DiscoverPage';
import { Overview } from '../components/pages/Overview';
import { SettingsPage } from '../components/pages/SettingsPage';
import { SubscriptionSettingsPage } from '../components/pages/SubscriptionSettingsPage';
import { TasksCenter } from '../components/pages/TasksCenter';
import { RssSeedLibraryPage } from '../components/pages/RssSeedLibraryPage';
import { usePolling } from '../hooks/usePolling';
import { getHomeSummary } from '../services/api';
import type { HomeSummaryResponse } from '../types/homeSummary';
import { defaultVisualFx, normalizeVisualFx } from '../types/visualFx';
import { pathForNavigation, readNavigation } from './navigation';

const VISUAL_FX_VERSION = '4';
const THEME_STORAGE_KEY = 'mcc-ui-theme';

function initialTheme(): ThemeMode {
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
  } catch {
    // Theme switching still works for the current session when storage is unavailable.
  }

  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function App() {
  const [navigation] = useState(readNavigation);
  const [page, setPage] = useState<PageId>(navigation.page);
  const [navigationTarget, setNavigationTarget] = useState<TaskNavigationTarget | null>(navigation.target);
  const [theme, setTheme] = useState<ThemeMode>(initialTheme);
  const [homeSummary, setHomeSummary] = useState<HomeSummaryResponse | null>(null);
  const [visualFx, setVisualFx] = useState(() => {
    try {
      const saved = window.localStorage.getItem('hallVisualFx');
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<typeof defaultVisualFx>;
        const shouldMigrateDefaults = window.localStorage.getItem('hallVisualFxVersion') !== VISUAL_FX_VERSION;
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

    const legacyPreset = Number(window.localStorage.getItem('hallVisualPreset'));
    return normalizeVisualFx({
      ...defaultVisualFx,
      preset: Number.isFinite(legacyPreset) ? legacyPreset : defaultVisualFx.preset
    });
  });

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
    window.localStorage.setItem('hallVisualFx', JSON.stringify(visualFx));
    window.localStorage.setItem('hallVisualFxVersion', VISUAL_FX_VERSION);
    window.localStorage.setItem('hallVisualPreset', String(visualFx.preset));
  }, [visualFx]);

  useEffect(() => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Keep the in-memory choice when storage is unavailable.
    }
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
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  }, [page]);

  useEffect(() => {
    const handlePopState = () => {
      const next = readNavigation();
      setPage(next.page);
      setNavigationTarget(['tasks', 'subscriptions'].includes(next.page) ? next.target : null);
    };
    window.addEventListener('popstate', handlePopState);
    if (!navigation.canonical) {
      const canonicalPath = pathForNavigation(navigation.page, navigation.target).split('?')[0];
      window.history.replaceState({}, '', `${canonicalPath}${navigation.search}`);
    }
    return () => window.removeEventListener('popstate', handlePopState);
  }, [navigation]);

  const navigate: AppNavigate = (nextPage, target) => {
    setPage(nextPage);
    const nextTarget = ['tasks', 'subscriptions'].includes(nextPage) ? target ?? null : null;
    setNavigationTarget(nextTarget);
    window.history.pushState({}, '', pathForNavigation(nextPage, nextTarget));
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
      {page === 'overview' && <Overview onNavigate={navigate} />}
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
      {page === 'rss-library' && <RssSeedLibraryPage />}
      {page === 'settings' && <SettingsPage />}
    </div>
  );
}
