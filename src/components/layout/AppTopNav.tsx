import { Activity, Bookmark, CalendarDays, Compass, Film, Home, ListChecks, Search, Settings } from 'lucide-react';
import type { HealthResponse } from '../../types/media';

export type PageId = 'overview' | 'hall' | 'control' | 'tasks' | 'calendar' | 'discover' | 'subscriptions' | 'subscription-settings' | 'settings';

const navItems: Array<{
  id: PageId;
  label: string;
  icon: typeof Home;
}> = [
  { id: 'overview', label: '总览', icon: Home },
  { id: 'hall', label: '影院大厅', icon: Film },
  { id: 'discover', label: '发现', icon: Compass },
  { id: 'subscriptions', label: '订阅', icon: Bookmark },
  { id: 'tasks', label: '任务中心', icon: ListChecks },
  { id: 'calendar', label: '日历', icon: CalendarDays }
];

interface AppTopNavProps {
  activePage: PageId;
  health: HealthResponse | null;
  onNavigate: (page: PageId) => void;
}

export function AppTopNav({ activePage, health, onNavigate }: AppTopNavProps) {
  const configuredCount = health?.services.filter((service) => service.configured).length ?? 0;
  const serviceCount = health?.services.length ?? 10;

  return (
    <header className="app-top-nav">
      <div className="nav-left-group">
        <button className="brand-lockup" type="button" onClick={() => onNavigate('hall')}>
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-copy">
            <span className="brand-title">媒体控制中心</span>
            <span className="brand-subtitle">私人影音中枢</span>
          </span>
        </button>

        <nav className="primary-nav" aria-label="主导航">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activePage === item.id || (item.id === 'subscriptions' && activePage === 'subscription-settings');

            return (
              <button
                aria-current={isActive ? 'page' : undefined}
                className={isActive ? 'nav-item nav-item--active' : 'nav-item'}
                key={item.id}
                type="button"
                onClick={() => onNavigate(item.id)}
              >
                <Icon aria-hidden="true" size={15} strokeWidth={1.8} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      <div className="nav-actions">
        <button
          className="nav-pill"
          type="button"
          onClick={() => {
            onNavigate('discover');
            window.setTimeout(() => window.dispatchEvent(new Event('mcc:focus-discover-search')), 0);
          }}
        >
          <Search aria-hidden="true" size={15} strokeWidth={1.8} />
          <span>搜索媒体</span>
        </button>
        <button className={activePage === 'control' ? 'nav-pill nav-pill--health nav-pill--active' : 'nav-pill nav-pill--health'} type="button" onClick={() => onNavigate('control')}>
          <Activity aria-hidden="true" size={15} strokeWidth={1.8} />
          <span>
            {configuredCount}/{serviceCount} 已配置
          </span>
        </button>
        <button
          aria-label="设置"
          className={activePage === 'settings' ? 'settings-button settings-button--active' : 'settings-button'}
          type="button"
          onClick={() => onNavigate('settings')}
        >
          <Settings aria-hidden="true" size={18} strokeWidth={1.8} />
        </button>
      </div>
    </header>
  );
}
