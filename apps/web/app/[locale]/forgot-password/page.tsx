"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { useApp } from "../../../components/app-context.tsx";
import { Shell } from "../../../components/shell.tsx";
import { Field, FormError, generalError, showErrors } from "../../../components/ui.tsx";
import { forgotPassword } from "../../../lib/api/auth.ts";
import { looksLikeEmail } from "../../../lib/validation/item.ts";

export default function ForgotPasswordPage() {
  const { t, locale, api } = useApp();
  const form = useRef<HTMLFormElement>(null);
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return; // already sending
    setFormError("");
    if (!looksLikeEmail(email)) {
      return showErrors(form.current, () => setError(t.auth.badEmail));
    }
    setError("");
    setBusy(true);
    try {
      await forgotPassword(api, email.trim());
      setSent(true);
    } catch (e) {
      setFormError(generalError(t, e));
    }
    setBusy(false);
  }

  return (
    <Shell>
      <div className="narrow">
        <h1 className="page-title">{t.auth.forgotTitle}</h1>
        <p className="muted">{t.auth.forgotIntro}</p>
        <FormError message={formError} />
        <p className="form-status" role="status">{sent ? t.auth.codeSent : ""}</p>
        <form ref={form} className="form-stack" method="post" onSubmit={submit} noValidate>
          <Field id="email" label={t.auth.email} type="email" autoComplete="email" dir="ltr" value={email}
            onChange={(e) => { setEmail(e.target.value); setError(""); }} error={error} required />
          <button className="btn btn-primary" type="submit" aria-busy={busy}>
            {busy ? t.common.loading : t.auth.sendCode}
          </button>
        </form>
        <div className="form-links">
          <Link href={`/${locale}/reset-password`}>{t.auth.haveCode}</Link>
          <Link href={`/${locale}/sign-in`}>{t.auth.backToSignIn}</Link>
        </div>
      </div>
    </Shell>
  );
}
