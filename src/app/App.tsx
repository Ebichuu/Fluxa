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
import { getHealth } from '../services/api';
import type { HealthResponse } from '../types/media';
import { defaultVisualFx, normalizeVisualFx } from '../types/visualFx';

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
  const [page, setPage] = useState<PageId>('hall');
  const [taskNavigationTarget, setTaskNavigationTarget] = useState<TaskNavigationTarget | null>(null);
  const [theme, setTheme] = useState<ThemeMode>(initialTheme);
  const [health, setHealth] = useState<HealthResponse | null>(null);
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

  useEffect(() => {
    let cancelled = false;

    getHealth()
      .then((nextHealth) => {
        if (!cancelled) {
          setHealth(nextHealth);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setHealth(null);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

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

  const navigate: AppNavigate = (nextPage, target) => {
    setPage(nextPage);
    setTaskNavigationTarget(nextPage === 'tasks' ? target ?? null : null);
  };

  return (
    <div className={`app-shell app-shell--${page}`} data-theme={page === 'hall' ? undefined : theme}>
      <AppTopNav
        activePage={page}
        health={health}
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
      {page === 'tasks' && <TasksCenter target={taskNavigationTarget} onClearTarget={() => setTaskNavigationTarget(null)} />}
      {page === 'calendar' && <CalendarPage onNavigate={navigate} />}
      {(page === 'discover' || page === 'subscriptions') && (
        <DiscoverPage onNavigate={navigate} view={page === 'subscriptions' ? 'subscriptions' : 'discover'} />
      )}
      {page === 'subscription-settings' && <SubscriptionSettingsPage onNavigate={navigate} />}
      {page === 'rss-library' && <RssSeedLibraryPage />}
      {page === 'settings' && <SettingsPage />}
    </div>
  );
}
