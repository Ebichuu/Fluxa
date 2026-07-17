import { useEffect, useRef, useState } from 'react';
import { Check, Layers3, ListMusic, Pin, PinOff, X } from 'lucide-react';
import type { MediaItem, MediaLibrary } from '../../types/media';

type QueuePanelTab = 'libraries' | 'queue';

interface MediaQueuePanelProps {
  activeIndex: number;
  activeLibraryId?: string;
  items: MediaItem[];
  libraries: MediaLibrary[];
  pinned: boolean;
  tab: QueuePanelTab;
  onPinnedChange: (pinned: boolean) => void;
  onTabChange: (tab: QueuePanelTab) => void;
  onSelectItem: (index: number) => void;
  onSelectLibrary: (libraryId: string) => void;
}

function itemMeta(item: MediaItem) {
  return [item.year, item.genres.slice(0, 2).join(' / '), item.type].filter(Boolean).join(' · ');
}

function libraryMeta(library: MediaLibrary) {
  const count = Number(library.itemCount || 0);
  return [library.collectionType || 'Library', count ? `${count} 项` : ''].filter(Boolean).join(' · ');
}

export function MediaQueuePanel({
  activeIndex,
  activeLibraryId,
  items,
  libraries,
  pinned,
  tab,
  onPinnedChange,
  onTabChange,
  onSelectItem,
  onSelectLibrary
}: MediaQueuePanelProps) {
  const [peeking, setPeeking] = useState(false);
  const activeRowRef = useRef<HTMLButtonElement | null>(null);
  const activeLibrary = libraries.find((library) => library.id === activeLibraryId);
  const activeItem = items[activeIndex];
  const panelOpen = pinned || peeking;

  useEffect(() => {
    if (panelOpen && tab === 'queue') {
      activeRowRef.current?.scrollIntoView({ block: 'nearest' });
    }
  }, [activeIndex, panelOpen, tab]);

  return (
    <aside
      className={`media-queue-panel${panelOpen ? ' media-queue-panel--open' : ''}${pinned ? ' media-queue-panel--pinned' : ''}`}
      aria-label="媒体库和本库内容"
      onMouseEnter={() => setPeeking(true)}
      onMouseLeave={() => setPeeking(false)}
      onWheel={(event) => event.stopPropagation()}
    >
      <button
        className="media-queue-panel__handle"
        type="button"
        aria-expanded={panelOpen}
        aria-label={panelOpen ? '收起媒体库浏览面板' : '展开媒体库浏览面板'}
        title="媒体库浏览"
        onClick={() => setPeeking((current) => !current)}
      >
        <ListMusic size={18} strokeWidth={1.8} />
      </button>

      <div className="media-queue-panel__head">
        <div className="media-queue-panel__title-block">
          <div className="media-queue-panel__title">媒体库浏览</div>
          <div className="media-queue-panel__sub">
            {activeLibrary ? activeLibrary.name : 'Media Center'} · {items.length} 项
          </div>
        </div>
        <div className="media-queue-panel__head-actions">
          <button
            className={`media-queue-panel__icon-button${pinned ? ' media-queue-panel__icon-button--active' : ''}`}
            type="button"
            aria-label={pinned ? '取消常开左侧面板' : '常开左侧面板'}
            title={pinned ? '取消常开' : '常开面板'}
            onClick={() => onPinnedChange(!pinned)}
          >
            {pinned ? <PinOff size={15} strokeWidth={1.9} /> : <Pin size={15} strokeWidth={1.9} />}
          </button>
          <button
            className="media-queue-panel__icon-button media-queue-panel__close"
            type="button"
            aria-label="关闭媒体库浏览面板"
            title="关闭"
            onClick={(event) => {
              event.currentTarget.blur();
              onPinnedChange(false);
              setPeeking(false);
            }}
          >
            <X size={15} strokeWidth={1.9} />
          </button>
        </div>
      </div>

      <div className="media-queue-panel__tabs" role="tablist" aria-label="媒体浏览方式">
        <button
          className={`media-queue-panel__tab${tab === 'libraries' ? ' media-queue-panel__tab--active' : ''}`}
          type="button"
          role="tab"
          aria-selected={tab === 'libraries'}
          onClick={() => onTabChange('libraries')}
        >
          <Layers3 size={14} strokeWidth={1.8} />
          <span>媒体库</span>
        </button>
        <button
          className={`media-queue-panel__tab${tab === 'queue' ? ' media-queue-panel__tab--active' : ''}`}
          type="button"
          role="tab"
          aria-selected={tab === 'queue'}
          onClick={() => onTabChange('queue')}
        >
          <ListMusic size={14} strokeWidth={1.8} />
          <span>本库内容</span>
        </button>
      </div>

      {tab === 'libraries' ? (
        <div className="media-queue-panel__list" role="tabpanel">
          {libraries.length ? (
            libraries.map((library) => {
              const selected = library.id === activeLibraryId;
              return (
                <button
                  className={`media-queue-panel__row${selected ? ' media-queue-panel__row--active' : ''}`}
                  type="button"
                  key={library.id}
                  onClick={() => onSelectLibrary(library.id)}
                >
                  <img src={library.posterUrl || library.backdropUrl} alt="" loading="lazy" decoding="async" />
                  <span className="media-queue-panel__row-copy">
                    <span className="media-queue-panel__row-title">{library.name}</span>
                    <span className="media-queue-panel__row-meta">{libraryMeta(library)}</span>
                  </span>
                  {selected && <Check className="media-queue-panel__row-check" size={15} strokeWidth={2} />}
                </button>
              );
            })
          ) : (
            <div className="media-queue-panel__empty">媒体库还没有返回数据</div>
          )}
        </div>
      ) : (
        <div className="media-queue-panel__list" role="tabpanel">
          {items.length ? (
            items.map((item, index) => {
              const selected = index === activeIndex;
              return (
                <button
                  className={`media-queue-panel__row${selected ? ' media-queue-panel__row--active' : ''}`}
                  type="button"
                  key={item.id}
                  ref={selected ? activeRowRef : undefined}
                  onClick={() => onSelectItem(index)}
                >
                  <img src={item.posterUrl || item.backdropUrl} alt="" loading="lazy" decoding="async" />
                  <span className="media-queue-panel__row-copy">
                    <span className="media-queue-panel__row-title">{item.title}</span>
                    <span className="media-queue-panel__row-meta">{itemMeta(item)}</span>
                  </span>
                  {selected && <Check className="media-queue-panel__row-check" size={15} strokeWidth={2} />}
                </button>
              );
            })
          ) : (
            <div className="media-queue-panel__empty">本库还没有可展示内容</div>
          )}
        </div>
      )}

      {activeItem && (
        <div className="media-queue-panel__now">
          <span>当前焦点</span>
          <strong>{activeItem.title}</strong>
        </div>
      )}
    </aside>
  );
}
