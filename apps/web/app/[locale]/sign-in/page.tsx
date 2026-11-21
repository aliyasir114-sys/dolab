"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { useApp } from "../../../components/app-context.tsx";
import { Shell } from "../../../components/shell.tsx";
import { Field, FormError, focusFirstInvalid, generalError } from "../../../components/ui.tsx";
import { ApiError } from "../../../lib/api/errors.ts";
import { looksLikeEmail } from "../../../lib/validation/item.ts";

/** Only go back to a page of this site after signing in. */
function safeNext(locale: string): string {
  const next = new URLSearchParams(window.location.search).get("next");
  return next && next.startsWith(`/${locale}/`) && !next.startsWith("//") ? next : `/${locale}/wardrobe`;
}

export default function SignInPage() {
  const { t, locale, session } = useApp();
  const router = useRouter();
  const form = useRef<HTMLFormElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found: typeof errors = {};
    if (!looksLikeEmail(email)) found.email = t.auth.badEmail;
    if (!password) found.password = t.common.required;
    setErrors(found);
    setFormError("");
    if (Object.keys(found).length) return focusFirstInvalid(form.current);
    setBusy(true);
    try {
      await session.signIn(email.trim(), password);
      router.replace(safeNext(locale));
    } catch (error) {
      setFormError(error instanceof ApiError && error.status === 401 ? t.auth.wrongCredentials : generalError(t, error));
      setBusy(false);
    }
  }

  return (
    <Shell>
      <div className="narrow">
        <h1 className="page-title">{t.auth.signInTitle}</h1>
        <FormError message={formError} />
        <form ref={form} className="form-stack" method="post" onSubmit={submit} noValidate>
          <Field id="email" label={t.auth.email} type="email" autoComplete="email" dir="ltr" value={email}
            onChange={(e) => setEmail(e.target.value)} error={errors.email} required />
          <Field id="password" label={t.auth.password} type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} error={errors.password} required />
          <button className="btn btn-primary" type="submit" disabled={busy} aria-busy={busy}>
            {busy ? t.common.loading : t.auth.signIn}
          </button>
        </form>
        <div className="form-links">
          <Link href={`/${locale}/forgot-password`}>{t.auth.forgot}</Link>
          <span>
            {t.auth.noAccount} <Link href={`/${locale}/sign-up`}>{t.auth.signUpTitle}</Link>
          </span>
        </div>
      </div>
    </Shell>
  );
}
