import { useEffect, useRef, useState } from 'react';
import { Bookmark, CalendarDays, Compass, LogOut, Moon, Search, Settings, Sun, Tv } from 'lucide-react';
import { api } from './services/api';
import type { AuthSession, DiscoverItem, MediaType } from './types/api';
import { DiscoverPage } from './pages/DiscoverPage';
import { HomePage } from './pages/HomePage';
import { LoginPage } from './pages/LoginPage';
import { MediaPipelinePage } from './pages/MediaPipelinePage';
import { SettingsPage } from './pages/SettingsPage';
import { SubscriptionsPage } from './pages/SubscriptionsPage';

type RouteState =
  | { page: 'home' }
  | { page: 'subscriptions' }
  | { page: 'discover' }
  | { page: 'settings' }
  | { page: 'media'; mediaType: MediaType; tmdbId: string };

function parseLocation(): RouteState {
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  const m = path.match(/^\/media\/(movie|tv)\/(\d+)$/);
  if (m) return { page: 'media', mediaType: m[1] as MediaType, tmdbId: m[2] };
  if (path === '/subscriptions') return { page: 'subscriptions' };
  if (path === '/discover') return { page: 'discover' };
  if (path === '/settings') return { page: 'settings' };
  return { page: 'home' };
}

function pathForRoute(route: RouteState): string {
  if (route.page === 'media') return `/media/${route.mediaType}/${route.tmdbId}`;
  if (route.page === 'home') return '/';
  return `/${route.page}`;
}

export function App() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [route, setRoute] = useState<RouteState>(parseLocation);
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const saved = localStorage.getItem('fluxa-theme');
    return saved === 'light' || saved === 'dark' ? saved : 'dark';
  });

  // Global search
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<DiscoverItem[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  const loadSession = async () => {
    try {
      const s = await api.getSession();
      setSession(s);
    } catch {
      setSession({ initialized: true, authenticated: false, username: '' });
    }
  };

  useEffect(() => {
    void loadSession();
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('fluxa-theme', theme);
  }, [theme]);

  useEffect(() => {
    const onPop = () => setRoute(parseLocation());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    const close = (e: PointerEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, []);

  useEffect(() => {
    const q = searchQuery.trim();
    if (!q || !session?.authenticated) {
      setSearchResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      api.search(q).then((r) => setSearchResults(r.items)).catch(() => setSearchResults([]));
    }, 260);
    return () => window.clearTimeout(timer);
  }, [searchQuery, session?.authenticated]);

  const navigate = (next: RouteState) => {
    setRoute(next);
    setSearchOpen(false);
    const targetPath = pathForRoute(next);
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
    window.scrollTo({ top: 0 });
  };

  const openMedia = (mediaType: MediaType, tmdbId: string) => {
    navigate({ page: 'media', mediaType, tmdbId });
  };

  const logout = async () => {
    await api.logout();
    await loadSession();
  };

  if (!session) {
    return <div className="auth-screen muted">正在连接 Fluxa…</div>;
  }

  if (!session.authenticated) {
    return <LoginPage initialized={session.initialized} onSuccess={() => void loadSession()} />;
  }

  const navItems: Array<{ page: 'home' | 'subscriptions' | 'discover' | 'settings'; label: string; icon: typeof CalendarDays }> = [
    { page: 'home', label: '首页', icon: CalendarDays },
    { page: 'subscriptions', label: '订阅', icon: Bookmark },
    { page: 'discover', label: '发现', icon: Compass },
    { page: 'settings', label: '设置', icon: Settings },
  ];

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand" onClick={() => navigate({ page: 'home' })}>
            <Tv size={19} color="var(--accent)" />
            <span className="brand-text">Fluxa</span>
            <span className="brand-badge">v2</span>
          </div>

          <nav className="desktop-nav" aria-label="主导航">
            {navItems.map(({ page, label, icon: Icon }) => (
              <button
                key={page}
                type="button"
                className={`nav-btn ${route.page === page ? 'active' : ''}`}
                onClick={() => navigate({ page })}
              >
                <Icon size={15} /> {label}
              </button>
            ))}
          </nav>

          <div className="search-box" ref={searchRef}>
            <Search size={15} className="search-icon" />
            <input
              className="search-input"
              type="text"
              placeholder="搜片名或 tv:12345 查卡在哪…"
              value={searchQuery}
              onFocus={() => setSearchOpen(true)}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setSearchOpen(true);
              }}
            />
            {searchOpen && searchQuery.trim().length > 0 && (
              <div className="search-dropdown">
                {!searchResults.length ? (
                  <div className="muted" style={{ padding: 12 }}>未找到匹配作品</div>
                ) : (
                  searchResults.map((item) => (
                    <button
                      key={item.mediaKey}
                      type="button"
                      className="search-item"
                      onClick={() => {
                        setSearchQuery('');
                        openMedia(item.mediaType, item.tmdbId);
                      }}
                    >
                      <span className="pill idle">{item.mediaType === 'tv' ? '剧' : '影'}</span>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontWeight: 600 }}>{item.title}</div>
                        <div className="muted">{item.year || ''} · TMDB {item.tmdbId}</div>
                      </div>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>

          <div className="topbar-actions">
            <button
              className="icon-btn"
              type="button"
              title="切换深色/浅色主题"
              onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
            >
              {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
            </button>
            <button className="icon-btn" type="button" title="退出登录" onClick={() => void logout()}>
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </header>

      <main className="main-content">
        {route.page === 'home' && (
          <HomePage onOpenMedia={openMedia} onGoSettings={() => navigate({ page: 'settings' })} />
        )}
        {route.page === 'media' && (
          <MediaPipelinePage
            mediaType={route.mediaType}
            tmdbId={route.tmdbId}
            onBack={() => navigate({ page: 'home' })}
          />
        )}
        {route.page === 'subscriptions' && <SubscriptionsPage onOpenMedia={openMedia} />}
        {route.page === 'discover' && (
          <DiscoverPage onOpenMedia={openMedia} onGoSettings={() => navigate({ page: 'settings' })} />
        )}
        {route.page === 'settings' && <SettingsPage />}
      </main>

      <nav className="mobile-nav" aria-label="移动端导航">
        {navItems.map(({ page, label, icon: Icon }) => (
          <button
            key={page}
            type="button"
            className={`mobile-nav-btn ${route.page === page ? 'active' : ''}`}
            onClick={() => navigate({ page })}
          >
            <Icon size={18} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
