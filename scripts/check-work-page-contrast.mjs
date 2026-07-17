const backgrounds = {
  page: '#08090b',
  panel: '#0e1014',
  console: '#020508'
};

const foregrounds = {
  ink: { color: '#e8ecef', minimum: 4.5 },
  strong: { color: '#f8f4ee', minimum: 4.5 },
  muted: { color: '#aeb7c0', minimum: 4.5 },
  faint: { color: '#929ca6', minimum: 4.5 },
  focus: { color: '#b7d6ff', minimum: 3 }
};

function rgb(hex) {
  const normalized = hex.replace('#', '');
  return [0, 2, 4].map((offset) => Number.parseInt(normalized.slice(offset, offset + 2), 16) / 255);
}

function luminance(hex) {
  const [red, green, blue] = rgb(hex).map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function ratio(foreground, background) {
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

let failed = false;
const rows = [];

for (const [foregroundName, foreground] of Object.entries(foregrounds)) {
  for (const [backgroundName, background] of Object.entries(backgrounds)) {
    const value = ratio(foreground.color, background);
    const passed = value >= foreground.minimum;
    failed ||= !passed;
    rows.push({
      foreground: foregroundName,
      background: backgroundName,
      ratio: value.toFixed(2),
      minimum: foreground.minimum.toFixed(1),
      result: passed ? 'PASS' : 'FAIL'
    });
  }
}

console.table(rows);
if (failed) process.exitCode = 1;
