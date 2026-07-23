import { useEffect, useMemo, useState } from 'react';
import { ImageOff } from 'lucide-react';

interface PosterImageProps {
  className: string;
  fallbackClassName?: string;
  fallbackVariant?: 'icon' | 'initial';
  loading?: 'eager' | 'lazy';
  src?: string;
  title: string;
}

const proxyHosts = [
  'image.tmdb.org',
  'doubanio.com',
  'iqiyipic.com',
  'qpic.cn'
];

function imageSource(value?: string) {
  const source = value?.trim() ?? '';
  if (!source) return '';
  try {
    const url = new URL(source, window.location.origin);
    if (url.origin === window.location.origin) return url.pathname + url.search;
    if (proxyHosts.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))) {
      return `/api/image?url=${encodeURIComponent(url.toString())}`;
    }
  } catch {
    return '';
  }
  return source;
}

export function PosterImage({ className, fallbackClassName, fallbackVariant = 'initial', loading = 'lazy', src, title }: PosterImageProps) {
  const resolvedSource = useMemo(() => imageSource(src), [src]);
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [resolvedSource]);

  if (!resolvedSource || failed) {
    return (
      <span aria-hidden="true" className={`${className} ${fallbackClassName ?? ''}`.trim()}>
        {fallbackVariant === 'icon' ? <ImageOff aria-hidden="true" size={22} strokeWidth={1.5} /> : title.trim().charAt(0) || '影'}
      </span>
    );
  }

  return (
    <img
      alt=""
      aria-hidden="true"
      className={className}
      decoding="async"
      loading={loading}
      src={resolvedSource}
      onError={() => setFailed(true)}
    />
  );
}
