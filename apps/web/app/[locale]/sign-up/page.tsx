"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { useApp } from "../../../components/app-context.tsx";
import { Shell } from "../../../components/shell.tsx";
import { Field, FormError, focusFirstInvalid, generalError } from "../../../components/ui.tsx";
import { register } from "../../../lib/api/auth.ts";
import { ApiError } from "../../../lib/api/errors.ts";
import { MAX_PASSWORD, MIN_PASSWORD, looksLikeEmail } from "../../../lib/validation/item.ts";

function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export default function SignUpPage() {
  const { t, locale, api, session } = useApp();
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
    if (password.length < MIN_PASSWORD || password.length > MAX_PASSWORD) found.password = t.auth.shortPassword;
    setErrors(found);
    setFormError("");
    if (Object.keys(found).length) return focusFirstInvalid(form.current);
    setBusy(true);
    try {
      await register(api, { email: email.trim(), password, locale, timezone: browserTimeZone() });
      await session.signIn(email.trim(), password);
      router.replace(`/${locale}/wardrobe`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setErrors({ email: t.auth.emailTaken });
        focusFirstInvalid(form.current);
      } else {
        setFormError(generalError(t, error));
      }
      setBusy(false);
    }
  }

  return (
    <Shell>
      <div className="narrow">
        <h1 className="page-title">{t.auth.signUpTitle}</h1>
        <FormError message={formError} />
        <form ref={form} className="form-stack" method="post" onSubmit={submit} noValidate>
          <Field id="email" label={t.auth.email} type="email" autoComplete="email" dir="ltr" value={email}
            onChange={(e) => setEmail(e.target.value)} error={errors.email} required />
          <Field id="password" label={t.auth.password} type="password" autoComplete="new-password" hint={t.auth.passwordHint}
            minLength={MIN_PASSWORD} maxLength={MAX_PASSWORD} value={password}
            onChange={(e) => setPassword(e.target.value)} error={errors.password} required />
          <button className="btn btn-primary" type="submit" disabled={busy} aria-busy={busy}>
            {busy ? t.common.loading : t.auth.signUp}
          </button>
        </form>
        <div className="form-links">
          <span>
            {t.auth.haveAccount} <Link href={`/${locale}/sign-in`}>{t.auth.signInTitle}</Link>
          </span>
        </div>
      </div>
    </Shell>
  );
}
