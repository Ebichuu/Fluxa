import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Search, X } from 'lucide-react';
import { searchMediaEverywhere } from '../../services/api';
import type { MediaSearchItem } from '../../types/mediaSearch';
import type { AppNavigate } from './AppTopNav';
import { PosterImage } from './PosterImage';

interface GlobalMediaSearchProps {
  open: boolean;
  onClose: () => void;
  onNavigate: AppNavigate;
}

function mediaTypeLabel(type: MediaSearchItem['mediaType']) {
  return type === 'tv' ? '剧集' : '电影';
}

export function GlobalMediaSearch({ open, onClose, onNavigate }: GlobalMediaSearchProps) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<MediaSearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab' || !panelRef.current) return;
      const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter((element) => element.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        panelRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!panelRef.current.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
      if (previouslyFocused?.isConnected) previouslyFocused.focus({ preventScroll: true });
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const keyword = query.trim();
    if (!keyword) {
      setItems([]);
      setError('');
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError('');
      searchMediaEverywhere(keyword, 10, { signal: controller.signal })
        .then((payload) => setItems(payload.items))
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '全局搜索暂不可用');
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 220);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query]);

  if (!open) return null;

  const openMedia = (item: MediaSearchItem) => {
    onClose();
    if (!item.tmdbId && item.chainId) {
      onNavigate('tasks', { chainId: item.chainId, title: item.title, userState: item.userState });
      return;
    }
    onNavigate('media', { mediaType: item.mediaType, tmdbId: item.tmdbId, title: item.title });
  };

  return (
    <div className="global-media-search" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section
        aria-labelledby="global-media-search-title"
        aria-modal="true"
        className="global-media-search__panel"
        ref={panelRef}
        role="dialog"
        tabIndex={-1}
      >
        <header>
          <div>
            <small>全局作品搜索</small>
            <h2 id="global-media-search-title">一处查看追更、下载、入库和播放状态</h2>
          </div>
          <button aria-label="关闭全局搜索" className="global-media-search__close" type="button" onClick={onClose}><X size={18} /></button>
        </header>
        <label className="global-media-search__input">
          <Search aria-hidden="true" size={18} />
          <input
            aria-label="搜索任意作品"
            placeholder="搜索《雀骨》或输入 tv:202"
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {loading && <span>搜索中</span>}
        </label>
        <div className="global-media-search__results" aria-live="polite">
          {!query.trim() && <div className="global-media-search__empty"><strong>输入作品名开始搜索</strong><span>结果会合并追更、任务、日历和 Emby 的本地证据。</span></div>}
          {query.trim() && !loading && error && <div className="global-media-search__empty is-error"><strong>搜索未完成</strong><span>{error}</span></div>}
          {query.trim() && !loading && !error && items.length === 0 && <div className="global-media-search__empty"><strong>本地没有找到“{query.trim()}”</strong><span>可以前往发现页搜索外部影视资料并添加追更。</span><button className="tool-link" type="button" onClick={() => { onClose(); onNavigate('discover'); }}>前往发现</button></div>}
          {items.map((item) => (
            <button className="global-media-search__result" key={item.mediaKey} type="button" onClick={() => openMedia(item)}>
              <PosterImage className="global-media-search__poster" fallbackClassName="global-media-search__poster--fallback" fallbackVariant="initial" src={item.posterUrl} title={item.title} />
              <span>
                <strong>{item.title}</strong>
                <small>{mediaTypeLabel(item.mediaType)}{item.year ? ` · ${item.year}` : ''} · {item.tmdbId ? `TMDB ${item.tmdbId}` : '身份待确认'}</small>
                <em>{item.resultText}</em>
              </span>
              <ArrowRight aria-hidden="true" size={17} />
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
