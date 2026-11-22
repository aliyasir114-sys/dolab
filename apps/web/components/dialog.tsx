"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A confirmation dialog in the design system's pattern (theme.css section 12, showcase.html):
 * while it is open the page behind is inert and does not scroll; Tab stays inside; Escape and the
 * close button cancel; on close, focus and the scroll position go back to where they were.
 * Focus starts on Cancel, so a stray Enter never confirms a deletion.
 */
export function ConfirmDialog({
  open, title, body, confirmLabel, cancelLabel, closeLabel, busy = false, error, onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  closeLabel: string;
  busy?: boolean;
  /** A failure shown inside the dialog (role="alert", empty until there is one). */
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  // Mounted hidden first, then shown, so the theme's fade runs.
  const [shown, setShown] = useState(false);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const scrollY = window.scrollY;
    const html = document.documentElement;
    const page = document.querySelector<HTMLElement>(".shell");
    html.classList.toggle("has-classic-scrollbar", window.innerWidth > html.clientWidth);
    html.classList.add("is-scroll-locked");
    if (page) page.inert = true;
    const frame = requestAnimationFrame(() => {
      setShown(true);
      cancel.current?.focus({ preventScroll: true });
    });

    function onKey(event: KeyboardEvent) {
      const dialog = box.current;
      if (!dialog) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onCancelRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    function keepFocus(event: FocusEvent) {
      const dialog = box.current;
      if (dialog && !dialog.contains(event.target as Node)) dialog.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", keepFocus);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", keepFocus);
      // The page must be interactive again before focus can return to it.
      if (page) page.inert = false;
      html.classList.remove("is-scroll-locked", "has-classic-scrollbar");
      window.scrollTo(0, scrollY);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
      setShown(false);
    };
  }, [open]);

  // The latest handler, without re-running the effect above on every render.
  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;

  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="modal-overlay" aria-hidden={shown ? "false" : "true"}>
      <div ref={box} className="modal-content" role="dialog" aria-modal="true"
        aria-labelledby={`${id}-title`} aria-describedby={`${id}-body`}>
        <div className="modal-header">
          <h2 id={`${id}-title`} className="modal-title">{title}</h2>
          <button type="button" className="modal-close" aria-label={closeLabel} onClick={onCancel}>
            <svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div id={`${id}-body`} className="modal-body">
          {body}
          <div className="form-error dialog-error" role="alert">{error ? <span>{error}</span> : null}</div>
        </div>
        <div className="modal-footer">
          <button ref={cancel} type="button" className="btn btn-secondary" onClick={onCancel}>{cancelLabel}</button>
          <button type="button" className="btn btn-primary" aria-busy={busy}
            onClick={() => { if (!busy) onConfirm(); }}>{confirmLabel}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
