import { useEffect, useState } from 'react';
import { AppTopNav, type PageId } from '../components/layout/AppTopNav';
import { MediaHall } from '../components/media-hall/MediaHall';
import { CalendarPage } from '../components/pages/CalendarPage';
import { ControlRoom } from '../components/pages/ControlRoom';
import { DiscoverPage } from '../components/pages/DiscoverPage';
import { Overview } from '../components/pages/Overview';
import { SettingsPage } from '../components/pages/SettingsPage';
import { SubscriptionSettingsPage } from '../components/pages/SubscriptionSettingsPage';
import { TasksCenter } from '../components/pages/TasksCenter';
import { getHealth } from '../services/api';
import type { HealthResponse } from '../types/media';
import { defaultVisualFx, normalizeVisualFx } from '../types/visualFx';

const VISUAL_FX_VERSION = '4';

export function App() {
  const [page, setPage] = useState<PageId>('hall');
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

  return (
    <div className={`app-shell app-shell--${page}`}>
      <AppTopNav activePage={page} health={health} onNavigate={setPage} />
      {page === 'overview' && <Overview onNavigate={setPage} />}
      {page === 'hall' && (
        <MediaHall
          visualFx={visualFx}
          onVisualFxChange={(nextVisualFx) =>
            setVisualFx((currentVisualFx) => normalizeVisualFx({ ...currentVisualFx, ...nextVisualFx }))
          }
        />
      )}
      {page === 'control' && <ControlRoom />}
      {page === 'tasks' && <TasksCenter />}
      {page === 'calendar' && <CalendarPage onNavigate={setPage} />}
      {page === 'discover' && <DiscoverPage onNavigate={setPage} />}
      {page === 'subscription-settings' && <SubscriptionSettingsPage onNavigate={setPage} />}
      {page === 'settings' && <SettingsPage />}
    </div>
  );
}
