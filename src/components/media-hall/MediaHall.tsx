import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getHomeMedia } from '../../services/api';
import type { HomeMediaResponse } from '../../types/media';
import type { VisualFxSettings } from '../../types/visualFx';
import { readLocalStorage, writeLocalStorage } from '../../utils/storage';
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
  const [queuePanelPinned, setQueuePanelPinned] = useState(() => readLocalStorage('mediaQueuePanelPinned') === '1');
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
    writeLocalStorage('mediaQueuePanelPinned', queuePanelPinned ? '1' : '0');
  }, [queuePanelPinned]);

  const items = useMemo(() => response?.items ?? [], [response?.items]);
  const libraries = useMemo(() => response?.libraries ?? [], [response?.libraries]);
  const currentLibrary = useMemo(() => libraries.find((library) => library.id === activeLibraryId), [libraries, activeLibraryId]);
  const activeItem = useMemo(() => items[activeIndex] ?? null, [items, activeIndex]);

  const moveFocus = useCallback((delta: number) => {
    if (items.length === 0) {
      return;
    }

    setActiveIndex((current) => (current + delta + items.length) % items.length);
  }, [items.length]);

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
  }, [moveFocus]);

  const handleWheel = useCallback((event: React.WheelEvent<HTMLElement>) => {
    if (wheelLockRef.current || event.altKey) {
      return;
    }

    wheelLockRef.current = true;
    moveFocus(event.deltaY > 0 ? 1 : -1);
    window.setTimeout(() => {
      wheelLockRef.current = false;
    }, 480);
  }, [moveFocus]);

  const handleSelectLibrary = useCallback((libraryId: string) => {
    if (libraryId && libraryId !== activeLibraryId) {
      setQueuePanelTab('queue');
      loadMedia(libraryId);
    }
  }, [activeLibraryId]);

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
        <span>{response?.source === 'emby' ? 'Emby 实时媒体库' : '示例媒体库'}</span>
        {currentLibrary && <span>{currentLibrary.name}</span>}
        {activeItem && <span>{activeItem.title}</span>}
        {!activeItem && <span>{error || '正在连接媒体库'}</span>}
      </div>
    </main>
  );
}
