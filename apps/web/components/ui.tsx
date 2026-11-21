"use client";

import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { ApiError } from "../lib/api/errors.ts";
import { fill, type Messages } from "../lib/i18n/messages.ts";

export function ErrorIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

/** The design system's error pattern: an empty role="alert" region, filled only when there is a
 *  problem, so it is announced then and not on page load. */
function ErrorRegion({ id, error }: { id: string; error?: string | null }) {
  return (
    <div id={id} className="error-message" role="alert">
      {error ? (
        <>
          <ErrorIcon />
          <span>{error}</span>
        </>
      ) : null}
    </div>
  );
}

type Common = { id: string; label: string; hint?: string; error?: string | null; optional?: string };

function describedBy(id: string, hint?: string) {
  return [hint ? `${id}-hint` : null, `${id}-error`].filter(Boolean).join(" ");
}

function Label({ id, label, optional }: { id: string; label: string; optional?: string }) {
  return (
    <label className="form-label" htmlFor={id}>
      {label}
      {optional ? <span className="form-optional"> {optional}</span> : null}
    </label>
  );
}

export function Field({ id, label, hint, error, optional, ...input }: Common & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="form-group">
      <Label id={id} label={label} optional={optional} />
      <input id={id} name={id} className="input-field" aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint)} {...input} />
      {hint ? <p id={`${id}-hint`} className="form-hint">{hint}</p> : null}
      <ErrorRegion id={`${id}-error`} error={error} />
    </div>
  );
}

export function SelectField({ id, label, hint, error, optional, children, ...select }: Common & SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <div className="form-group">
      <Label id={id} label={label} optional={optional} />
      <select id={id} name={id} className="input-field" aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint)} {...select}>
        {children}
      </select>
      {hint ? <p id={`${id}-hint`} className="form-hint">{hint}</p> : null}
      <ErrorRegion id={`${id}-error`} error={error} />
    </div>
  );
}

/** A problem with the whole form (role="alert", empty until there is one). */
export function FormError({ message }: { message: string }) {
  return (
    <div className="form-error" role="alert">
      {message ? (
        <>
          <ErrorIcon />
          <span>{message}</span>
        </>
      ) : null}
    </div>
  );
}

/** The usual words for a failed request; pages handle their own specific statuses first. */
export function generalError(t: Messages, error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return t.common.network;
    if (error.status === 429) return fill(t.common.tooMany, { minutes: Math.max(1, Math.ceil((error.retryAfter ?? 60) / 60)) });
    if (error.status === 401) return t.common.sessionOver;
  }
  return t.common.unexpected;
}

/** Move focus to the first field marked invalid, after React has rendered the errors. */
export function focusFirstInvalid(form: HTMLFormElement | null) {
  requestAnimationFrame(() => form?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
}
