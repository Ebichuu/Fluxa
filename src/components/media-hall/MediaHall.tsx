import { useEffect, useRef, useState } from 'react';
import { getHomeMedia } from '../../services/api';
import type { HomeMediaResponse } from '../../types/media';
import type { VisualFxSettings } from '../../types/visualFx';
import { MineradioEmbed } from './MineradioEmbed';
import { MediaQueuePanel } from './MediaQueuePanel';

interface MediaHallProps {
  visualFx: VisualFxSettings;
  onVisualFxChange?: (visualFx: Partial<VisualFxSettings>) => void;
}

export function MediaHall({ visualFx, onVisualFxChange }: MediaHallProps) {
  const [response, setResponse] = useState<HomeMediaResponse | null>(null);
  const [error, setError] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [activeLibraryId, setActiveLibraryId] = useState<string | undefined>();
  const [queuePanelPinned, setQueuePanelPinned] = useState(() => window.localStorage.getItem('mediaQueuePanelPinned') === '1');
  const [queuePanelTab, setQueuePanelTab] = useState<'libraries' | 'queue'>('libraries');
  const requestIdRef = useRef(0);
  const wheelLockRef = useRef(false);

  const loadMedia = (libraryId?: string) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;

    getHomeMedia(libraryId)
      .then((nextResponse) => {
        if (requestIdRef.current === requestId) {
          const nextLibraryId = nextResponse.activeLibraryId ?? libraryId ?? nextResponse.libraries[0]?.id;
          setResponse(nextResponse);
          setError('');
          setActiveLibraryId(nextLibraryId);
          setActiveIndex(0);
        }
      })
      .catch((requestError: unknown) => {
        if (requestIdRef.current === requestId) {
          setError(requestError instanceof Error ? requestError.message : '首页媒体加载失败');
        }
      });
  };

  useEffect(() => {
    loadMedia();

    return () => {
      requestIdRef.current += 1;
    };
  }, []);

  useEffect(() => {
    window.localStorage.setItem('mediaQueuePanelPinned', queuePanelPinned ? '1' : '0');
  }, [queuePanelPinned]);

  const items = response?.items ?? [];
  const libraries = response?.libraries ?? [];
  const currentLibrary = libraries.find((library) => library.id === activeLibraryId);
  const activeItem = items[activeIndex] ?? null;

  const moveFocus = (delta: number) => {
    if (items.length === 0) {
      return;
    }

    setActiveIndex((current) => (current + delta + items.length) % items.length);
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        moveFocus(1);
      }
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        moveFocus(-1);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [items.length]);

  const handleWheel = (event: React.WheelEvent<HTMLElement>) => {
    if (wheelLockRef.current || event.altKey) {
      return;
    }

    wheelLockRef.current = true;
    moveFocus(event.deltaY > 0 ? 1 : -1);
    window.setTimeout(() => {
      wheelLockRef.current = false;
    }, 480);
  };

  const handleSelectLibrary = (libraryId: string) => {
    if (libraryId && libraryId !== activeLibraryId) {
      setQueuePanelTab('queue');
      loadMedia(libraryId);
    }
  };

  return (
    <main className="media-hall media-hall--mineradio-embed" onWheel={handleWheel}>
      <MineradioEmbed
        activeItem={activeItem}
        activeLibraryId={activeLibraryId}
        items={items}
        libraries={libraries}
        visualFx={visualFx}
        onVisualFxChange={onVisualFxChange}
        onSelectItem={setActiveIndex}
        onSelectLibrary={handleSelectLibrary}
      />
      <MediaQueuePanel
        activeIndex={activeIndex}
        activeLibraryId={activeLibraryId}
        items={items}
        libraries={libraries}
        pinned={queuePanelPinned}
        tab={queuePanelTab}
        onPinnedChange={setQueuePanelPinned}
        onTabChange={setQueuePanelTab}
        onSelectItem={setActiveIndex}
        onSelectLibrary={handleSelectLibrary}
      />
      <div className="mineradio-embed-status" aria-live="polite">
        <span>{response?.source === 'emby' ? 'Emby Live Library' : 'Sample Library'}</span>
        {currentLibrary && <span>{currentLibrary.name}</span>}
        {activeItem && <span>{activeItem.title}</span>}
        {!activeItem && <span>{error || '正在连接媒体库'}</span>}
      </div>
    </main>
  );
}
