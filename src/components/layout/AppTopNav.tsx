import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Activity, Bookmark, CalendarDays, Compass, Film, Home, ListChecks, Moon, Search, Settings, Sun } from 'lucide-react';
import type { HomeSummaryResponse } from '../../types/homeSummary';
import { healthStatusLabel } from '../status/HealthBadge';

export type PageId = 'overview' | 'hall' | 'control' | 'tasks' | 'calendar' | 'discover' | 'subscriptions' | 'subscription-settings' | 'rss-library' | 'settings';
export type ThemeMode = 'dark' | 'light';

export interface TaskNavigationTarget {
  mediaType?: 'movie' | 'tv';
  chainId?: string;
  targetKey?: string;
  subscriptionId?: string;
  tmdbId?: string;
  title?: string;
  seasonNumber?: number | null;
}

export type AppNavigate = (page: PageId, target?: TaskNavigationTarget) => void;

const navItems: Array<{
  id: PageId;
  label: string;
  icon: typeof Home;
  mobileHidden?: boolean;
}> = [
  { id: 'overview', label: '首页', icon: Home },
  { id: 'discover', label: '发现', icon: Compass },
  { id: 'subscriptions', label: '追更', icon: Bookmark },
  { id: 'tasks', label: '任务中心', icon: ListChecks },
  { id: 'calendar', label: '日历', icon: CalendarDays }
];

interface AppTopNavProps {
  activePage: PageId;
  homeSummary: HomeSummaryResponse | null;
  onNavigate: AppNavigate;
  onToggleTheme: () => void;
  showThemeToggle: boolean;
  theme: ThemeMode;
}

export function AppTopNav({ activePage, homeSummary, onNavigate, onToggleTheme, showThemeToggle, theme }: AppTopNavProps) {
  const healthState = homeSummary?.healthState ?? 'evidence_insufficient';
  const healthLabel = !homeSummary
    ? '状态读取中'
    : homeSummary.counts.actionRequired > 0
      ? `${homeSummary.counts.actionRequired} 项需要处理`
      : healthState === 'waiting'
        ? '任务处理中'
        : healthState === 'normal'
          ? '运行正常'
          : healthStatusLabel(healthState);
  const navRef = useRef<HTMLElement>(null);
  const itemRefs = useRef(new Map<PageId, HTMLButtonElement>());
  const selectionRef = useRef<HTMLSpanElement>(null);
  const selectionAnimationRef = useRef<Animation | null>(null);
  const selectionStartRectRef = useRef<{ left: number; width: number } | null>(null);
  const selectionTargetRef = useRef<{ left: number; width: number } | null>(null);
  const managementRef = useRef<HTMLDivElement>(null);
  const activeNavId = activePage === 'subscription-settings' ? 'subscriptions' : navItems.some((item) => item.id === activePage) ? activePage : null;
  const [selection, setSelection] = useState({ left: 0, width: 0, visible: false });
  const [isScrolled, setIsScrolled] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);

  useEffect(() => setManagementOpen(false), [activePage]);

  useEffect(() => {
    if (!managementOpen) return undefined;
    const close = (event: PointerEvent) => {
      if (!managementRef.current?.contains(event.target as Node)) setManagementOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [managementOpen]);

  useEffect(() => {
    if (!showThemeToggle) {
      setIsScrolled(false);
      return undefined;
    }

    const updateScrollState = () => setIsScrolled(window.scrollY > 12);
    updateScrollState();
    window.addEventListener('scroll', updateScrollState, { passive: true });
    return () => window.removeEventListener('scroll', updateScrollState);
  }, [showThemeToggle]);

  useLayoutEffect(() => {
    const nav = navRef.current;
    const activeItem = activeNavId ? itemRefs.current.get(activeNavId) : null;
    if (!nav || !activeItem) {
      selectionAnimationRef.current?.cancel();
      selectionAnimationRef.current = null;
      selectionStartRectRef.current = null;
      selectionTargetRef.current = null;
      setSelection((current) => ({ ...current, visible: false }));
      return undefined;
    }

    const updateSelection = () => {
      const nextTarget = { left: activeItem.offsetLeft, width: activeItem.offsetWidth };
      const currentTarget = selectionTargetRef.current;
      if (currentTarget?.left === nextTarget.left && currentTarget.width === nextTarget.width) {
        setSelection((current) => current.visible ? current : { ...nextTarget, visible: true });
        return;
      }

      selectionStartRectRef.current = null;
      const indicator = selectionRef.current;
      if (indicator) {
        const currentRect = indicator.getBoundingClientRect();
        if (currentRect.width > 0) {
          selectionStartRectRef.current = { left: currentRect.left, width: currentRect.width };
        }
      }

      selectionAnimationRef.current?.cancel();
      selectionAnimationRef.current = null;
      selectionTargetRef.current = nextTarget;
      setSelection({ ...nextTarget, visible: true });
    };
    updateSelection();

    if (nav.scrollWidth > nav.clientWidth) {
      const navRect = nav.getBoundingClientRect();
      const activeRect = activeItem.getBoundingClientRect();
      if (activeRect.left < navRect.left || activeRect.right > navRect.right) {
        activeItem.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    }

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updateSelection);
    observer?.observe(nav);
    observer?.observe(activeItem);
    window.addEventListener('resize', updateSelection);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', updateSelection);
    };
  }, [activeNavId]);

  useLayoutEffect(() => {
    const indicator = selectionRef.current;
    const startRect = selectionStartRectRef.current;
    selectionStartRectRef.current = null;
    if (!indicator || !selection.visible || !startRect || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const endRect = indicator.getBoundingClientRect();
    if (!endRect.width) return;

    const deltaX = startRect.left - endRect.left;
    const scaleX = startRect.width / endRect.width;
    if (Math.abs(deltaX) < 0.5 && Math.abs(scaleX - 1) < 0.005) return;

    const animation = indicator.animate(
      [
        { transform: `translate3d(${deltaX}px, 0, 0) scaleX(${scaleX})` },
        { transform: 'translate3d(0, 0, 0) scaleX(1)' }
      ],
      { duration: 280, easing: 'cubic-bezier(0.2, 0, 0, 1)' }
    );
    selectionAnimationRef.current = animation;
    const clearAnimation = () => {
      if (selectionAnimationRef.current === animation) selectionAnimationRef.current = null;
    };
    animation.onfinish = clearAnimation;
    animation.oncancel = clearAnimation;
  }, [selection.left, selection.visible, selection.width]);

  return (
    <header className={isScrolled ? 'app-top-nav app-top-nav--scrolled' : 'app-top-nav'}>
      <div className="nav-left-group">
        <button className="brand-lockup" type="button" onClick={() => onNavigate('hall')}>
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-copy">
            <span className="brand-title">Fluxa</span>
            <span className="brand-subtitle">私人影音中枢</span>
          </span>
        </button>

        <nav className="primary-nav" aria-label="主导航" ref={navRef}>
          <span
            aria-hidden="true"
            className={selection.visible ? 'primary-nav__selection is-visible' : 'primary-nav__selection'}
            ref={selectionRef}
            style={{ left: selection.left, width: selection.width }}
          />
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activePage === item.id || (item.id === 'subscriptions' && activePage === 'subscription-settings');

            return (
              <button
                aria-current={isActive ? 'page' : undefined}
                className={isActive ? 'nav-item nav-item--active' : 'nav-item'}
                data-mobile-hidden={item.mobileHidden || undefined}
                key={item.id}
                ref={(element) => {
                  if (element) itemRefs.current.set(item.id, element);
                  else itemRefs.current.delete(item.id);
                }}
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
          aria-label="进入影院大厅"
          className={activePage === 'hall' ? 'settings-button nav-hall-entry settings-button--active' : 'settings-button nav-hall-entry'}
          title="进入影院大厅"
          type="button"
          onClick={() => onNavigate('hall')}
        >
          <Film aria-hidden="true" size={18} strokeWidth={1.8} />
        </button>
        <button
          aria-label="搜索媒体"
          className="nav-pill"
          title="搜索媒体"
          type="button"
          onClick={() => {
            onNavigate('discover');
            window.setTimeout(() => window.dispatchEvent(new Event('mcc:focus-discover-search')), 0);
          }}
        >
          <Search aria-hidden="true" size={15} strokeWidth={1.8} />
          <span>搜索媒体</span>
        </button>
        <button
          aria-label={`${healthLabel}，打开控制室`}
          className={activePage === 'control' ? 'nav-pill nav-pill--health nav-pill--active' : 'nav-pill nav-pill--health'}
          data-health={healthState}
          title={`${healthLabel}，打开控制室`}
          type="button"
          onClick={() => onNavigate('control')}
        >
          <Activity aria-hidden="true" size={15} strokeWidth={1.8} />
          <span>
            {healthLabel}
          </span>
        </button>
        {showThemeToggle && (
          <button
            aria-label={theme === 'dark' ? '切换到白天模式' : '切换到夜间模式'}
            aria-pressed={theme === 'dark'}
            className="theme-toggle"
            data-theme={theme}
            title={theme === 'dark' ? '切换到白天模式' : '切换到夜间模式'}
            type="button"
            onClick={onToggleTheme}
          >
            <Sun aria-hidden="true" className="theme-toggle__icon theme-toggle__icon--sun" size={18} strokeWidth={1.8} />
            <Moon aria-hidden="true" className="theme-toggle__icon theme-toggle__icon--moon" size={18} strokeWidth={1.8} />
            <span className="sr-only">当前为{theme === 'dark' ? '夜间' : '白天'}模式</span>
          </button>
        )}
        <div className="nav-management" ref={managementRef}>
          <button
            aria-expanded={managementOpen}
            aria-haspopup="menu"
            aria-label="管理菜单"
            className={activePage === 'settings' || activePage === 'control' ? 'settings-button settings-button--active' : 'settings-button'}
            title="设置"
            type="button"
            onClick={() => {
              if (window.matchMedia('(max-width: 760px)').matches) setManagementOpen((current) => !current);
              else onNavigate('settings');
            }}
          >
            <Settings aria-hidden="true" size={18} strokeWidth={1.8} />
          </button>
          {managementOpen && (
            <div className="nav-management__menu" role="menu">
              <button role="menuitem" type="button" onClick={() => onNavigate('control')}>
                <Activity aria-hidden="true" size={16} />
                <span><strong>控制室</strong><small>连接、能力与诊断</small></span>
              </button>
              <button role="menuitem" type="button" onClick={() => onNavigate('settings')}>
                <Settings aria-hidden="true" size={16} />
                <span><strong>设置</strong><small>偏好与通知</small></span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
