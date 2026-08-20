export function subscriptionYearOptions(
  items: Array<{ year?: string }>,
  selectedYear = 'all',
  latestYear = new Date().getFullYear() + 1
) {
  const years = new Set<string>();
  [...items.map((item) => item.year), selectedYear].forEach((value) => {
    const year = String(value || '').trim();
    const numericYear = Number(year);
    if (/^\d{4}$/.test(year) && numericYear >= 1900 && numericYear <= latestYear) {
      years.add(year);
    }
  });
  return [...years].sort((left, right) => Number(right) - Number(left));
}
