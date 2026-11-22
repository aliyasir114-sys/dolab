"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useApp } from "../../../components/app-context.tsx";
import { ConfirmDialog } from "../../../components/dialog.tsx";
import { Shell } from "../../../components/shell.tsx";
import { ChoiceChips, Field, FormError, FormStatus, SelectField, generalError, showErrors } from "../../../components/ui.tsx";
import { useRequireSession } from "../../../components/use-require-session.ts";
import { getProfile, saveProfile, updateAccount } from "../../../lib/api/account.ts";
import { me } from "../../../lib/api/auth.ts";
import { ApiError } from "../../../lib/api/errors.ts";
import { serverLifecycle } from "../../../lib/api/lifecycle.ts";
import type { Locale, Profile } from "../../../lib/api/types.ts";
import { MAX_PASSWORD, MIN_PASSWORD } from "../../../lib/validation/item.ts";

const SIZES = ["S", "M", "L", "XL", "XXL"];
const BODY = ["rectangle", "triangle", "inverted_triangle", "oval", "hourglass"] as const;
const FACE = ["oval", "round", "square", "heart", "long"] as const;

type Measures = { height: string; weight: string; size: string; body: string; face: string };
const EMPTY: Measures = { height: "", weight: "", size: "", body: "", face: "" };

function measuresOf(p: Profile): Measures {
  return {
    height: p.height_cm == null ? "" : String(p.height_cm),
    weight: p.weight_kg == null ? "" : String(p.weight_kg),
    size: p.usual_size ?? "",
    body: p.body_shape ?? "",
    face: p.face_shape ?? "",
  };
}

/** A number within the server's range, or null when left empty; undefined when it is wrong. */
function numberIn(raw: string, min: number, max: number): number | null | undefined {
  const text = raw.trim().replace(",", ".");
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= min && value <= max ? value : undefined;
}

function timeZones(current: string): string[] {
  let zones: string[] = [];
  try {
    zones = (Intl as unknown as { supportedValuesOf(key: string): string[] }).supportedValuesOf("timeZone");
  } catch {
    zones = [];
  }
  return zones.includes(current) ? zones : [current, ...zones];
}

export default function ProfilePage() {
  const { t, locale, api, session } = useApp();
  const router = useRouter();
  const ready = useRequireSession();
  const lifecycle = useMemo(() => serverLifecycle(api), [api]);

  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [measures, setMeasures] = useState<Measures>(EMPTY);
  const [measureErrors, setMeasureErrors] = useState<{ height?: string; weight?: string }>({});
  const [account, setAccount] = useState<{ locale: Locale; timezone: string }>({ locale, timezone: "UTC" });
  const [passwords, setPasswords] = useState({ current: "", next: "" });
  const [passwordErrors, setPasswordErrors] = useState<{ current?: string; next?: string }>({});
  // Each section has its own messages, next to its own button.
  const [messages, setMessages] = useState<Record<string, { error?: string; status?: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<null | "clear" | "delete">(null);
  const [dialogError, setDialogError] = useState("");
  const working = useRef(false);
  const measureForm = useRef<HTMLFormElement>(null);
  const passwordForm = useRef<HTMLFormElement>(null);

  const say = (section: string, value: { error?: string; status?: string }) => setMessages((m) => ({ ...m, [section]: value }));

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const [profile, user] = await Promise.all([getProfile(api), me(api)]);
      setMeasures(measuresOf(profile));
      setAccount({ locale: user.locale, timezone: user.timezone });
      setLoaded(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace(`/${locale}/sign-in`);
      else setLoadError(generalError(t, e));
    }
  }, [api, locale, router, t]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  async function run(section: string, action: () => Promise<string>, onError?: (e: unknown) => string | null) {
    if (working.current) return;
    working.current = true;
    setBusy(section);
    say(section, {});
    try {
      say(section, { status: await action() });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace(`/${locale}/sign-in`);
      else say(section, { error: onError?.(e) ?? generalError(t, e) });
    }
    working.current = false;
    setBusy(null);
  }

  function saveMeasures(event: FormEvent) {
    event.preventDefault();
    const height = numberIn(measures.height, 50, 260);
    const weight = numberIn(measures.weight, 20, 400);
    const found = {
      height: height === undefined ? t.profile.heightRange : undefined,
      weight: weight === undefined ? t.profile.weightRange : undefined,
    };
    showErrors(measureForm.current, () => setMeasureErrors(found));
    if (height === undefined || weight === undefined) return;
    void run("measures", async () => {
      const saved = await saveProfile(api, {
        height_cm: height, weight_kg: weight,
        usual_size: measures.size || null, body_shape: measures.body || null, face_shape: measures.face || null,
      });
      setMeasures(measuresOf(saved));
      return t.profile.saved;
    });
  }

  function saveSettings(event: FormEvent) {
    event.preventDefault();
    void run("settings", async () => {
      const user = await updateAccount(api, account);
      setAccount({ locale: user.locale, timezone: user.timezone });
      return t.profile.saved;
    });
  }

  function changePassword(event: FormEvent) {
    event.preventDefault();
    const found = {
      current: passwords.current ? undefined : t.common.required,
      next: passwords.next.length < MIN_PASSWORD || passwords.next.length > MAX_PASSWORD ? t.auth.shortPassword : undefined,
    };
    showErrors(passwordForm.current, () => setPasswordErrors(found));
    if (found.current || found.next) return;
    void run(
      "password",
      async () => {
        await session.changePassword(passwords.current, passwords.next);
        setPasswords({ current: "", next: "" });
        return t.profile.passwordChanged;
      },
      (e) => (e instanceof ApiError && e.status === 403 ? t.profile.wrongCurrent : e instanceof ApiError && e.status === 422 ? t.auth.shortPassword : null),
    );
  }

  function exportData() {
    void run("privacy", async () => {
      const file = await lifecycle.exportAccount();
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = `dolab-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return t.profile.exported;
    });
  }

  async function confirmPending() {
    if (!pending || working.current) return;
    working.current = true;
    setBusy("dialog");
    setDialogError("");
    try {
      if (pending === "delete") {
        await lifecycle.deleteAccount();
        await session.signOut();
        router.replace(`/${locale}/sign-in`);
        return;
      }
      await lifecycle.clearProfile();
      setMeasures(EMPTY);
      setPending(null);
      say("privacy", { status: t.profile.cleared });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace(`/${locale}/sign-in`);
      else setDialogError(generalError(t, e));
    } finally {
      working.current = false;
      setBusy(null);
    }
  }

  if (!ready || !loaded) {
    return (
      <Shell signedIn>
        <FormError message={loadError} />
        {loadError ? (
          <button type="button" className="btn btn-secondary" onClick={() => void load()}>{t.common.retry}</button>
        ) : (
          <p className="muted">{t.common.loading}</p>
        )}
      </Shell>
    );
  }

  const none = { value: "", label: t.profile.noChoice };
  const zones = timeZones(account.timezone);

  return (
    <Shell signedIn>
      <div className="profile">
        <div>
          <h1 className="page-title">{t.profile.title}</h1>
          <p className="muted">{t.profile.intro}</p>
        </div>

        <section className="card profile-section" aria-labelledby="measures-title">
          <h2 id="measures-title" className="section-title">{t.profile.measurements}</h2>
          <form ref={measureForm} className="form-stack" method="post" onSubmit={saveMeasures} noValidate>
            <div className="field-pair">
              <Field id="height" label={t.profile.height} inputMode="decimal" dir="ltr" value={measures.height}
                error={measureErrors.height}
                onChange={(e) => { setMeasures((m) => ({ ...m, height: e.target.value })); setMeasureErrors((x) => ({ ...x, height: undefined })); }} />
              <Field id="weight" label={t.profile.weight} inputMode="decimal" dir="ltr" value={measures.weight}
                error={measureErrors.weight}
                onChange={(e) => { setMeasures((m) => ({ ...m, weight: e.target.value })); setMeasureErrors((x) => ({ ...x, weight: undefined })); }} />
            </div>
            <ChoiceChips name="size" legend={t.profile.usualSize} value={measures.size}
              options={[none, ...SIZES.map((s) => ({ value: s, label: s }))]}
              onChange={(size) => setMeasures((m) => ({ ...m, size }))} />
            <ChoiceChips name="body" legend={t.profile.bodyShape} value={measures.body}
              options={[none, ...BODY.map((s) => ({ value: s, label: t.profile.bodyShapes[s] }))]}
              onChange={(body) => setMeasures((m) => ({ ...m, body }))} />
            <ChoiceChips name="face" legend={t.profile.faceShape} value={measures.face}
              options={[none, ...FACE.map((s) => ({ value: s, label: t.profile.faceShapes[s] }))]}
              onChange={(face) => setMeasures((m) => ({ ...m, face }))} />
            <p className="form-hint">{t.profile.note}</p>
            <FormError message={messages.measures?.error ?? ""} />
            <FormStatus message={messages.measures?.status ?? ""} />
            <div className="form-actions">
              <button type="submit" className="btn btn-primary" aria-busy={busy === "measures"}>{t.profile.saveMeasurements}</button>
            </div>
          </form>
        </section>

        <section className="card profile-section" aria-labelledby="settings-title">
          <h2 id="settings-title" className="section-title">{t.profile.settings}</h2>
          <form className="form-stack" method="post" onSubmit={saveSettings}>
            <div className="field-pair stack-on-phone">
              <SelectField id="account-locale" label={t.profile.language} hint={t.profile.languageHint} value={account.locale}
                onChange={(e) => setAccount((a) => ({ ...a, locale: e.target.value as Locale }))}>
                <option value="ar" lang="ar">العربية</option>
                <option value="en" lang="en">English</option>
              </SelectField>
              <SelectField id="timezone" label={t.profile.timezone} hint={t.profile.timezoneHint} value={account.timezone} dir="ltr"
                onChange={(e) => setAccount((a) => ({ ...a, timezone: e.target.value }))}>
                {zones.map((zone) => (
                  <option key={zone} value={zone}>{zone}</option>
                ))}
              </SelectField>
            </div>
            <FormError message={messages.settings?.error ?? ""} />
            <FormStatus message={messages.settings?.status ?? ""} />
            <div className="form-actions">
              <button type="submit" className="btn btn-primary" aria-busy={busy === "settings"}>{t.profile.saveSettings}</button>
            </div>
          </form>

          <h3 className="subsection-title">{t.profile.passwordTitle}</h3>
          <form ref={passwordForm} className="form-stack" method="post" onSubmit={changePassword} noValidate>
            <div className="field-pair stack-on-phone">
              <Field id="current-password" label={t.profile.currentPassword} type="password" autoComplete="current-password"
                value={passwords.current} error={passwordErrors.current}
                onChange={(e) => { setPasswords((p) => ({ ...p, current: e.target.value })); setPasswordErrors((x) => ({ ...x, current: undefined })); }} />
              <Field id="new-password" label={t.auth.newPassword} type="password" autoComplete="new-password" hint={t.auth.passwordHint}
                maxLength={MAX_PASSWORD} value={passwords.next} error={passwordErrors.next}
                onChange={(e) => { setPasswords((p) => ({ ...p, next: e.target.value })); setPasswordErrors((x) => ({ ...x, next: undefined })); }} />
            </div>
            <FormError message={messages.password?.error ?? ""} />
            <FormStatus message={messages.password?.status ?? ""} />
            <div className="form-actions">
              <button type="submit" className="btn btn-secondary" aria-busy={busy === "password"}>{t.profile.changePassword}</button>
            </div>
          </form>
        </section>

        <section className="card profile-section" aria-labelledby="privacy-title">
          <h2 id="privacy-title" className="section-title">{t.profile.privacy}</h2>
          <p className="form-hint" id="export-hint">{t.profile.exportHint}</p>
          <FormError message={messages.privacy?.error ?? ""} />
          <FormStatus message={messages.privacy?.status ?? ""} />
          <div className="form-actions privacy-actions">
            <button type="button" className="btn btn-secondary" aria-describedby="export-hint" aria-busy={busy === "privacy"} onClick={exportData}>
              {t.profile.exportButton}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => { setDialogError(""); setPending("clear"); }}>
              {t.profile.clearProfile}
            </button>
            <button type="button" className="btn btn-primary" onClick={() => { setDialogError(""); setPending("delete"); }}>
              {t.profile.deleteAccount}
            </button>
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={pending !== null}
        title={pending === "delete" ? t.profile.deleteAccountTitle : t.profile.clearProfileTitle}
        body={<p>{pending === "delete" ? t.profile.deleteAccountBody : t.profile.clearProfileBody}</p>}
        confirmLabel={pending === "delete" ? t.profile.deleteAccountConfirm : t.profile.clearProfileConfirm}
        cancelLabel={t.common.cancel}
        closeLabel={t.common.closeDialog}
        busy={busy === "dialog"}
        error={dialogError}
        onConfirm={() => void confirmPending()}
        onCancel={() => { if (busy !== "dialog") setPending(null); }}
      />
    </Shell>
  );
}
