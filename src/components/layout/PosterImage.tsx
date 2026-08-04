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
const failedPosterSources = new Set<string>();

function isSameOrigin(value: string) {
  try {
    return new URL(value, window.location.origin).origin === window.location.origin;
  } catch {
    return false;
  }
}

function isEmptyPoster(image: HTMLImageElement, source: string) {
  if (image.naturalWidth === 0 || image.naturalHeight === 0) return true;
  if (!isSameOrigin(source)) return false;
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 24;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) return false;
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const total = pixels.length / 4;
    let transparent = 0;
    let visible = 0;
    let white = 0;
    let brightnessSum = 0;
    let brightnessSquaredSum = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      const red = pixels[index];
      const green = pixels[index + 1];
      const blue = pixels[index + 2];
      const alpha = pixels[index + 3];
      if (alpha === 0) {
        transparent += 1;
        continue;
      }
      visible += 1;
      if (red >= 250 && green >= 250 && blue >= 250) white += 1;
      const brightness = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      brightnessSum += brightness;
      brightnessSquaredSum += brightness * brightness;
    }
    if (transparent / total > 0.99) return true;
    if (visible === 0 || white / visible <= 0.99) return false;
    const mean = brightnessSum / visible;
    const variance = Math.max(0, brightnessSquaredSum / visible - mean * mean);
    return Math.sqrt(variance) < 2;
  } catch {
    return false;
  }
}

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
  const [failed, setFailed] = useState(() => failedPosterSources.has(resolvedSource));

  useEffect(() => setFailed(!resolvedSource || failedPosterSources.has(resolvedSource)), [resolvedSource]);

  const rejectSource = () => {
    if (resolvedSource) failedPosterSources.add(resolvedSource);
    setFailed(true);
  };

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
      onError={rejectSource}
      onLoad={(event) => {
        if (isEmptyPoster(event.currentTarget, resolvedSource)) rejectSource();
      }}
    />
  );
}
