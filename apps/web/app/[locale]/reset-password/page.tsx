"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { useApp } from "../../../components/app-context.tsx";
import { Shell } from "../../../components/shell.tsx";
import { Field, FormError, focusFirstInvalid, generalError } from "../../../components/ui.tsx";
import { resetPassword } from "../../../lib/api/auth.ts";
import { ApiError } from "../../../lib/api/errors.ts";
import { MAX_PASSWORD, MIN_PASSWORD } from "../../../lib/validation/item.ts";

export default function ResetPasswordPage() {
  const { t, locale, api } = useApp();
  const form = useRef<HTMLFormElement>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ code?: string; password?: string }>({});
  const [formError, setFormError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found: typeof errors = {};
    if (code.trim().length < 10) found.code = t.auth.badCode;
    if (password.length < MIN_PASSWORD || password.length > MAX_PASSWORD) found.password = t.auth.shortPassword;
    setErrors(found);
    setFormError("");
    if (Object.keys(found).length) return focusFirstInvalid(form.current);
    setBusy(true);
    try {
      await resetPassword(api, code.trim(), password);
      setDone(true);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 400 || error.status === 422)) {
        setErrors({ code: t.auth.badCode });
        focusFirstInvalid(form.current);
      } else {
        setFormError(generalError(t, error));
      }
    }
    setBusy(false);
  }

  return (
    <Shell>
      <div className="narrow">
        <h1 className="page-title">{t.auth.resetTitle}</h1>
        <FormError message={formError} />
        <p className="form-status" role="status">{done ? t.auth.resetDone : ""}</p>
        {done ? null : (
          <form ref={form} className="form-stack" onSubmit={submit} noValidate>
            <Field id="code" label={t.auth.code} autoComplete="one-time-code" dir="ltr" value={code}
              onChange={(e) => setCode(e.target.value)} error={errors.code} required />
            <Field id="password" label={t.auth.newPassword} type="password" autoComplete="new-password" hint={t.auth.passwordHint}
              minLength={MIN_PASSWORD} maxLength={MAX_PASSWORD} value={password}
              onChange={(e) => setPassword(e.target.value)} error={errors.password} required />
            <button className="btn btn-primary" type="submit" disabled={busy} aria-busy={busy}>
              {busy ? t.common.loading : t.auth.resetSubmit}
            </button>
          </form>
        )}
        <div className="form-links">
          <Link href={`/${locale}/sign-in`}>{t.auth.backToSignIn}</Link>
        </div>
      </div>
    </Shell>
  );
}
