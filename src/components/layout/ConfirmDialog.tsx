import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode
} from 'react';
import { createPortal } from 'react-dom';

interface ConfirmDialogProps {
  busy?: boolean;
  children: ReactNode;
  className?: string;
  describedBy?: string;
  labelledBy: string;
  open: boolean;
  onClose: () => void;
}

const DIALOG_EXIT_MS = 220;

const focusableSelector = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])'
].join(',');

function focusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector))
    .filter((element) => element.getClientRects().length > 0);
}

function reducedMotionEnabled() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function ConfirmDialog({ busy = false, children, className = '', describedBy, labelledBy, open, onClose }: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const pointerStartRef = useRef<{ id: number; x: number; y: number } | null>(null);
  const [rendered, setRendered] = useState(open);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let frame = 0;
    let timer = 0;

    if (open) {
      setRendered(true);
      frame = window.requestAnimationFrame(() => setVisible(true));
    } else {
      setVisible(false);
      timer = window.setTimeout(() => setRendered(false), reducedMotionEnabled() ? 0 : DIALOG_EXIT_MS);
    }

    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [open]);

  useEffect(() => {
    if (!rendered) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [rendered]);

  useEffect(() => {
    if (!open || !rendered) return undefined;
    triggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    if (!dialog) return undefined;

    const initialFocus = dialog.querySelector<HTMLElement>('[data-dialog-initial-focus]')
      ?? focusableElements(dialog)[0]
      ?? dialog;
    const frame = window.requestAnimationFrame(() => initialFocus.focus({ preventScroll: true }));

    return () => {
      window.cancelAnimationFrame(frame);
      const trigger = triggerRef.current;
      window.setTimeout(() => {
        if (trigger?.isConnected && !(trigger instanceof HTMLButtonElement && trigger.disabled)) trigger.focus();
      }, reducedMotionEnabled() ? 0 : DIALOG_EXIT_MS);
    };
  }, [open, rendered]);

  const requestClose = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      requestClose();
      return;
    }
    if (event.key !== 'Tab') return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = focusableElements(dialog);
    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }

    const currentIndex = focusable.indexOf(document.activeElement as HTMLElement);
    const atStart = currentIndex <= 0;
    const atEnd = currentIndex === focusable.length - 1;
    if (event.shiftKey && atStart) {
      event.preventDefault();
      focusable[focusable.length - 1].focus();
    } else if (!event.shiftKey && (atEnd || currentIndex < 0)) {
      event.preventDefault();
      focusable[0].focus();
    }
  };

  const handleBackdropPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || busy) return;
    pointerStartRef.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handleBackdropPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = pointerStartRef.current;
    pointerStartRef.current = null;
    if (!start || start.id !== event.pointerId || event.target !== event.currentTarget) return;
    const distance = Math.hypot(event.clientX - start.x, event.clientY - start.y);
    if (distance <= 6) requestClose();
  };

  if (!rendered) return null;

  return createPortal((
    <div
      className={visible && open ? 'ops-confirm-backdrop is-open' : 'ops-confirm-backdrop'}
      role="presentation"
      onPointerCancel={() => { pointerStartRef.current = null; }}
      onPointerDown={handleBackdropPointerDown}
      onPointerUp={handleBackdropPointerUp}
    >
      <section
        aria-describedby={describedBy}
        aria-labelledby={labelledBy}
        aria-modal="true"
        className={`ops-confirm-dialog ${className}`.trim()}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        {children}
      </section>
    </div>
  ), document.body);
}
