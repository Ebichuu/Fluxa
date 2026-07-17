import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

const horizontalKeys = new Set(['ArrowLeft', 'ArrowRight', 'Home', 'End']);

export function handleHorizontalTabKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
  if (!horizontalKeys.has(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;

  const tabList = event.currentTarget.closest<HTMLElement>('[role="tablist"]');
  if (!tabList) return;

  const tabs = Array.from(tabList.querySelectorAll<HTMLElement>('[role="tab"]')).filter((tab) => {
    if (tab.closest('[role="tablist"]') !== tabList) return false;
    if (tab.getAttribute('aria-disabled') === 'true') return false;
    return !(tab instanceof HTMLButtonElement && tab.disabled);
  });
  const currentIndex = tabs.indexOf(event.currentTarget);
  if (currentIndex < 0 || tabs.length === 0) return;

  let nextIndex = currentIndex;
  if (event.key === 'Home') nextIndex = 0;
  if (event.key === 'End') nextIndex = tabs.length - 1;
  if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;

  event.preventDefault();
  const nextTab = tabs[nextIndex];
  nextTab.focus();
  nextTab.click();
}
