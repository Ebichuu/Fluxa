import { useEffect, useMemo, useRef, useState } from 'react';
import type { MediaItem, MediaLibrary } from '../../types/media';
import type { VisualFxSettings } from '../../types/visualFx';

interface MineradioEmbedProps {
  activeItem: MediaItem | null;
  activeLibraryId?: string;
  items: MediaItem[];
  libraries: MediaLibrary[];
  visualFx: VisualFxSettings;
  onVisualFxChange?: (visualFx: Partial<VisualFxSettings>) => void;
  onSelectItem?: (index: number) => void;
  onSelectLibrary?: (libraryId: string) => void;
}

export function MineradioEmbed({
  activeItem,
  activeLibraryId,
  items,
  libraries,
  visualFx,
  onVisualFxChange,
  onSelectItem,
  onSelectLibrary
}: MineradioEmbedProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [bridgeReady, setBridgeReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [startupSlow, setStartupSlow] = useState(false);

  const libraryIds = useMemo(() => new Set(libraries.map((library) => library.id)), [libraries]);
  const payload = useMemo(
    () => ({
      activeItem,
      activeLibraryId,
      items,
      libraries,
      visualFx
    }),
    [activeItem, activeLibraryId, items, libraries, visualFx]
  );

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) {
        return;
      }
      if (event.source !== frameRef.current?.contentWindow) {
        return;
      }
      if (event.data?.type === 'mineradio:ready') {
        setBridgeReady(true);
        setStartupSlow(false);
        setLoadFailed(false);
      }
      if (event.data?.type === 'mineradio:item-select') {
        const index = Number(event.data.index);
        if (Number.isInteger(index) && index >= 0 && index < items.length) {
          onSelectItem?.(index);
        }
      }
      if (event.data?.type === 'mineradio:library-select') {
        const libraryId = String(event.data.libraryId || '');
        if (libraryId && libraryIds.has(libraryId)) {
          onSelectLibrary?.(libraryId);
        }
      }
      if (event.data?.type === 'mineradio:visual-fx-change' && typeof event.data.visualFx === 'object') {
        onVisualFxChange?.(event.data.visualFx as Partial<VisualFxSettings>);
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [items.length, libraryIds, onSelectItem, onSelectLibrary, onVisualFxChange]);

  useEffect(() => {
    const contentWindow = frameRef.current?.contentWindow;
    if (!contentWindow || (!frameLoaded && !bridgeReady)) {
      return;
    }

    contentWindow.postMessage({ type: 'mcc:mineradio-data', payload }, window.location.origin);
  }, [payload, frameLoaded, bridgeReady]);

  useEffect(() => {
    if (bridgeReady || loadFailed) {
      return;
    }

    const timeout = window.setTimeout(() => {
      setStartupSlow(true);
    }, 8000);

    return () => window.clearTimeout(timeout);
  }, [bridgeReady, loadFailed, frameLoaded]);

  const statusText = loadFailed
    ? 'Mineradio 视觉加载失败'
    : startupSlow
      ? 'Mineradio 视觉仍在启动'
      : '正在加载 Mineradio 视觉';

  return (
    <section className="mineradio-embed" aria-label="Mineradio 首页视觉">
      <iframe
        ref={frameRef}
        className="mineradio-embed__frame"
        title="Mineradio visual stage"
        src="/mineradio/embed"
        allow="autoplay; fullscreen"
        onLoad={() => {
          setFrameLoaded(true);
          setLoadFailed(false);
        }}
        onError={() => {
          setLoadFailed(true);
        }}
      />
      {(!bridgeReady || loadFailed) && (
        <div className="mineradio-embed__state" role="status" aria-live="polite">
          <span>{statusText}</span>
        </div>
      )}
    </section>
  );
}
