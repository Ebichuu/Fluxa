export interface VisualFxSettings {
  preset: number;
  intensity: number;
  depth: number;
  coverResolution: number;
  point: number;
  speed: number;
  twist: number;
  color: number;
  scatter: number;
  bgFade: number;
  bloom: number;
  bloomEnabled: boolean;
  edgeEnabled: boolean;
  cinema: boolean;
  shelfAngle: number;
  shelfOpacity: number;
  shelfBgOpacity: number;
  shelfPresence: 'auto' | 'always';
  shelfSize: number;
  shelfOffsetX: number;
  shelfOffsetY: number;
  shelfOffsetZ: number;
}

export const defaultVisualFx: VisualFxSettings = {
  preset: 0,
  intensity: 0.85,
  depth: 1,
  coverResolution: 1.55,
  point: 1,
  speed: 1,
  twist: 0,
  color: 1.1,
  scatter: 0,
  bgFade: 0.2,
  bloom: 0.62,
  bloomEnabled: false,
  edgeEnabled: false,
  cinema: true,
  shelfAngle: 0,
  shelfOpacity: 1,
  shelfBgOpacity: 0.9,
  shelfPresence: 'always',
  shelfSize: 0.82,
  shelfOffsetX: -0.76,
  shelfOffsetY: 0,
  shelfOffsetZ: 0
};

export function normalizeVisualFx(value: Partial<VisualFxSettings> | null | undefined): VisualFxSettings {
  return {
    preset: clampNumber(value?.preset, 0, 6, defaultVisualFx.preset),
    intensity: clampNumber(value?.intensity, 0.2, 1.6, defaultVisualFx.intensity),
    depth: clampNumber(value?.depth, 0.2, 1.8, defaultVisualFx.depth),
    coverResolution: clampNumber(value?.coverResolution, 0.75, 1.55, defaultVisualFx.coverResolution),
    point: clampNumber(value?.point, 0.5, 2.2, defaultVisualFx.point),
    speed: clampNumber(value?.speed, 0.2, 2.5, defaultVisualFx.speed),
    twist: clampNumber(value?.twist, 0, 0.6, defaultVisualFx.twist),
    color: clampNumber(value?.color, 0.5, 2, defaultVisualFx.color),
    scatter: clampNumber(value?.scatter, 0, 0.5, defaultVisualFx.scatter),
    bgFade: clampNumber(value?.bgFade, 0, 1.2, defaultVisualFx.bgFade),
    bloom: clampNumber(value?.bloom, 0, 1.6, defaultVisualFx.bloom),
    bloomEnabled: value?.bloomEnabled === true,
    edgeEnabled: value?.edgeEnabled === true,
    cinema: value?.cinema !== false,
    shelfAngle: clampNumber(value?.shelfAngle, -30, 30, defaultVisualFx.shelfAngle),
    shelfOpacity: clampNumber(value?.shelfOpacity, 0.25, 1, defaultVisualFx.shelfOpacity),
    shelfBgOpacity: clampNumber(value?.shelfBgOpacity, 0.25, 0.98, defaultVisualFx.shelfBgOpacity),
    shelfPresence: value?.shelfPresence === 'auto' ? 'auto' : 'always',
    shelfSize: clampNumber(value?.shelfSize, 0.65, 1.45, defaultVisualFx.shelfSize),
    shelfOffsetX: clampNumber(value?.shelfOffsetX, -1.2, 1.2, defaultVisualFx.shelfOffsetX),
    shelfOffsetY: clampNumber(value?.shelfOffsetY, -0.9, 0.9, defaultVisualFx.shelfOffsetY),
    shelfOffsetZ: clampNumber(value?.shelfOffsetZ, -0.9, 0.9, defaultVisualFx.shelfOffsetZ)
  };
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }

  return Math.max(min, Math.min(max, numeric));
}
