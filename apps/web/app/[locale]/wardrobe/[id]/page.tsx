"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useApp } from "../../../../components/app-context.tsx";
import { ConfirmDialog } from "../../../../components/dialog.tsx";
import { Shell } from "../../../../components/shell.tsx";
import { Field, FormError, FormStatus, SelectField, generalError, showErrors } from "../../../../components/ui.tsx";
import { useRequireSession } from "../../../../components/use-require-session.ts";
import { me } from "../../../../lib/api/auth.ts";
import { ApiError } from "../../../../lib/api/errors.ts";
import { getItem, itemHistory, updateItem, wearItem } from "../../../../lib/api/items.ts";
import { serverLifecycle } from "../../../../lib/api/lifecycle.ts";
import { MAX_PHOTOS_PER_ITEM, PHOTO_TYPES } from "../../../../lib/api/photos.ts";
import type { Item, ItemStatus, Photo, StatusChange } from "../../../../lib/api/types.ts";
import { daysAgo, formatDay, formatMoment, formatPrice, itemRef, monthsBetween, todayIn } from "../../../../lib/format.ts";
import { fill, plural, type Messages } from "../../../../lib/i18n/messages.ts";
import { canWear, statusTargets } from "../../../../lib/status.ts";
import { checkPhotos, validateItem, type ItemFieldError, type ItemForm } from "../../../../lib/validation/item.ts";

type Pending = null | "deleteItem" | { photo: Photo };

function formOf(item: Item): ItemForm {
  return {
    name: item.name,
    category: item.category,
    primaryColor: item.primary_color ?? "",
    status: "available", // not edited here; status has its own control
    purchaseDate: item.purchase_date ?? "",
    price: item.purchase_price ?? "",
    currency: item.currency ?? "",
  };
}

function fieldMessage(t: Messages, error: ItemFieldError): string {
  if (error.code === "required") return t.common.required;
  if (error.code === "tooLong") return fill(t.item.tooLong, { max: error.max });
  return t.item[error.code];
}

export default function ItemPage() {
  const { t, locale, api, photos } = useApp();
  const router = useRouter();
  const ready = useRequireSession();
  const id = String(useParams<{ id: string }>().id ?? "");
  const lifecycle = useMemo(() => serverLifecycle(api), [api]);

  const [item, setItem] = useState<Item | null>(null);
  const [gallery, setGallery] = useState<Photo[]>([]);
  const [shown, setShown] = useState(0);
  const [history, setHistory] = useState<StatusChange[]>([]);
  const [today, setToday] = useState(() => todayIn());
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [nextStatus, setNextStatus] = useState<ItemStatus | "">("");
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<ItemForm | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof ItemForm, string>>>({});
  const [pending, setPending] = useState<Pending>(null);
  const [dialogError, setDialogError] = useState("");
  const editForm = useRef<HTMLFormElement>(null);
  const working = useRef(false);

  const fail = useCallback(
    (e: unknown, where: (message: string) => void = setError) => {
      if (e instanceof ApiError && e.status === 401) router.replace(`/${locale}/sign-in`);
      else if (e instanceof ApiError && e.status === 404) setMissing(true);
      else if (e instanceof ApiError && e.status === 409) where(t.itemPage.conflict);
      else where(generalError(t, e));
    },
    [locale, router, t],
  );

  const load = useCallback(async () => {
    setError("");
    try {
      const [found, list, changes, account] = await Promise.all([
        getItem(api, id), photos.list(id), itemHistory(api, id), me(api).catch(() => null),
      ]);
      setItem(found);
      setGallery(list);
      setHistory(changes);
      if (account) setToday(todayIn(account.timezone));
    } catch (e) {
      fail(e);
    }
  }, [api, id, photos, fail]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  /** One action at a time; each says what it did in the status region. */
  async function run(name: string, action: () => Promise<string | void>) {
    if (working.current) return;
    working.current = true;
    setBusy(name);
    setError("");
    setNotice("");
    try {
      const done = await action();
      if (done) setNotice(done);
    } catch (e) {
      fail(e);
    }
    working.current = false;
    setBusy(null);
  }

  const refreshHistory = () => itemHistory(api, id).then(setHistory).catch(() => undefined);

  function wear() {
    void run("wear", async () => {
      setItem(await wearItem(api, id));
      return t.itemPage.wornToday;
    });
  }

  function changeStatus(event: FormEvent) {
    event.preventDefault();
    if (!nextStatus) return;
    void run("status", async () => {
      setItem(await updateItem(api, id, { status: nextStatus }));
      setNextStatus("");
      await refreshHistory();
      return t.itemPage.statusChanged;
    });
  }

  function addPhotos(files: File[]) {
    if (!item) return;
    const room = MAX_PHOTOS_PER_ITEM - gallery.length;
    const problems = checkPhotos(files.slice(0, room));
    if (files.length > room) {
      setError(t.item.tooManyPhotos);
      return;
    }
    if (problems.length) {
      setError(problems.map((p) => (p.code === "tooManyPhotos" ? t.item.tooManyPhotos : fill(t.item[p.code], { name: p.name }))).join(" "));
      return;
    }
    void run("upload", async () => {
      for (const file of files) await photos.upload(id, file, file.name);
      const list = await photos.list(id);
      setGallery(list);
      setShown(list.length - 1);
      setItem(await getItem(api, id)); // the cover may have changed
      return t.itemPage.photoAdded;
    });
  }

  function startEdit() {
    if (!item) return;
    setValues(formOf(item));
    setFieldErrors({});
    setEditing(true);
  }

  function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!values) return;
    const { errors, input } = validateItem(values, today);
    const found: typeof fieldErrors = {};
    for (const e of errors) found[e.field] ??= fieldMessage(t, e);
    showErrors(editForm.current, () => setFieldErrors(found));
    if (!input) return;
    void run("edit", async () => {
      const { status: _ignored, ...patch } = input;
      setItem(await updateItem(api, id, patch));
      setEditing(false);
      return t.itemPage.edited;
    });
  }

  async function confirmPending() {
    if (!pending || working.current) return;
    working.current = true;
    setBusy("dialog");
    setDialogError("");
    try {
      if (pending === "deleteItem") {
        await lifecycle.deleteItem(id);
        router.replace(`/${locale}/wardrobe`);
        return;
      }
      await photos.remove(id, pending.photo.id);
      const list = await photos.list(id);
      setGallery(list);
      setShown((n) => Math.min(n, Math.max(0, list.length - 1)));
      setItem(await getItem(api, id));
      setPending(null);
    } catch (e) {
      fail(e, setDialogError);
    } finally {
      working.current = false;
      setBusy(null);
    }
  }

  const set = (key: keyof ItemForm) => (event: { target: { value: string } }) => {
    setValues((v) => (v ? { ...v, [key]: event.target.value } : v));
    setFieldErrors((e) => ({ ...e, [key]: undefined }));
  };

  const back = (
    <Link className="back-link" href={`/${locale}/wardrobe`}>
      {t.itemPage.back}
    </Link>
  );

  if (missing) {
    return (
      <Shell signedIn>
        {back}
        <div className="card empty-state">
          <p>{t.itemPage.notFound}</p>
        </div>
      </Shell>
    );
  }

  if (!ready || !item) {
    return (
      <Shell signedIn>
        {back}
        <FormError message={error} />
        {error ? (
          <button type="button" className="btn btn-secondary" onClick={() => void load()}>{t.common.retry}</button>
        ) : (
          <p className="muted">{t.common.loading}</p>
        )}
      </Shell>
    );
  }

  const current = gallery[shown] ?? gallery[0];
  const targets = statusTargets(item.status);
  const lastWorn = item.last_worn_at
    ? daysAgo(item.last_worn_at, today, { today: t.itemPage.today, yesterday: t.itemPage.yesterday, days: t.itemPage.daysAgo }, locale)
    : t.itemPage.neverWorn;
  const months = item.purchase_date ? monthsBetween(item.purchase_date, today) : null;
  const age = months === null ? t.common.notSet : months === 0 ? t.itemPage.ageUnderMonth : plural(t.itemPage.ageMonths, months, locale);

  return (
    <Shell signedIn>
      {back}
      <div className="item-page">
        <section className="item-gallery" aria-labelledby="photos-title">
          <h2 id="photos-title" className="visually-hidden">{t.itemPage.photosTitle}</h2>
          <div className="gallery-main">
            {current ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={api.mediaUrl(current.url)} alt={fill(t.itemPage.showPhoto, { n: shown + 1 })} />
            ) : (
              <span>{t.itemPage.noPhotos}</span>
            )}
          </div>
          {gallery.length > 1 ? (
            <ul className="gallery-thumbs">
              {gallery.map((photo, index) => (
                <li key={photo.id}>
                  <button type="button" className="thumb" aria-pressed={index === shown}
                    aria-label={fill(t.itemPage.showPhoto, { n: index + 1 })} onClick={() => setShown(index)}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={api.mediaUrl(photo.thumb_url)} alt="" />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="gallery-actions">
            {gallery.length < MAX_PHOTOS_PER_ITEM ? (
              <>
                <input id="add-photos" type="file" className="file-input visually-hidden" multiple
                  accept={PHOTO_TYPES.join(",")} aria-describedby="add-photos-hint"
                  onChange={(e) => {
                    const files = Array.from(e.target.files ?? []);
                    e.target.value = "";
                    if (files.length) addPhotos(files);
                  }} />
                <label htmlFor="add-photos" className="btn btn-secondary file-button" aria-busy={busy === "upload"}>
                  {busy === "upload" ? t.itemPage.uploading : t.itemPage.addPhotos}
                </label>
              </>
            ) : null}
            {current ? (
              <button type="button" className="btn btn-secondary" onClick={() => { setDialogError(""); setPending({ photo: current }); }}>
                {t.itemPage.deletePhoto}
              </button>
            ) : null}
          </div>
          <p id="add-photos-hint" className="form-hint">
            {gallery.length < MAX_PHOTOS_PER_ITEM ? t.item.photosHint : t.itemPage.photosFull}
          </p>
        </section>

        <section className="item-info">
          <div className="item-heading">
            <h1 className="page-title">{item.name}</h1>
            {/* The Latin code isolated inside the paragraph, so it sits under the name on the reading side. */}
            <p className="muted"><span dir="ltr">{itemRef(item.id)}</span></p>
            <span className="status-pill" data-status={item.status}>{t.status[item.status]}</span>
          </div>

          <FormError message={error} />
          <FormStatus message={notice} />

          <div className="item-actions">
            {canWear(item.status) ? (
              <button type="button" className="btn btn-primary" aria-busy={busy === "wear"} onClick={wear}>
                {t.itemPage.wearToday}
              </button>
            ) : (
              <p className="muted">{t.itemPage.cannotWear}</p>
            )}
          </div>

          <section className="card item-section" aria-labelledby="details-title">
            <div className="section-head">
              <h2 id="details-title" className="section-title">{t.itemPage.details}</h2>
              {!editing ? (
                <button type="button" className="btn btn-secondary btn-small" onClick={startEdit}>{t.itemPage.edit}</button>
              ) : null}
            </div>
            {editing && values ? (
              <form ref={editForm} className="form-stack" method="post" onSubmit={saveEdit} noValidate>
                <Field id="name" label={t.item.name} maxLength={120} value={values.name} onChange={set("name")} error={fieldErrors.name} required />
                <Field id="category" label={t.item.category} maxLength={60} value={values.category} onChange={set("category")} error={fieldErrors.category} required />
                <Field id="primaryColor" label={t.item.color} optional={t.item.optional} maxLength={40}
                  value={values.primaryColor} onChange={set("primaryColor")} error={fieldErrors.primaryColor} />
                <Field id="purchaseDate" label={t.item.purchaseDate} optional={t.item.optional} type="date" max={today}
                  value={values.purchaseDate} onChange={set("purchaseDate")} error={fieldErrors.purchaseDate} />
                <div className="field-pair">
                  <Field id="price" label={t.item.price} optional={t.item.optional} inputMode="decimal" dir="ltr"
                    value={values.price} onChange={set("price")} error={fieldErrors.price} />
                  <Field id="currency" label={t.item.currency} optional={t.item.optional} placeholder={t.item.currencyPlaceholder}
                    maxLength={3} autoCapitalize="characters" value={values.currency} onChange={set("currency")} error={fieldErrors.currency} />
                </div>
                <div className="form-actions">
                  <button type="submit" className="btn btn-primary" aria-busy={busy === "edit"}>
                    {busy === "edit" ? t.item.saving : t.itemPage.saveChanges}
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={() => setEditing(false)}>{t.common.cancel}</button>
                </div>
              </form>
            ) : (
              <dl className="details">
                <div><dt>{t.item.category}</dt><dd>{item.category}</dd></div>
                <div><dt>{t.item.color}</dt><dd>{item.primary_color ?? t.common.notSet}</dd></div>
                <div><dt>{t.itemPage.wearCount}</dt><dd>{item.wear_count}</dd></div>
                <div><dt>{t.itemPage.lastWorn}</dt><dd>{lastWorn}</dd></div>
                <div><dt>{t.itemPage.age}</dt><dd>{age}</dd></div>
                <div><dt>{t.item.purchaseDate}</dt><dd>{item.purchase_date ? formatDay(item.purchase_date, locale) : t.common.notSet}</dd></div>
                <div>
                  <dt>{t.item.price}</dt>
                  <dd>{item.purchase_price ? <bdi>{formatPrice(item.purchase_price, item.currency, locale)}</bdi> : t.common.notSet}</dd>
                </div>
              </dl>
            )}
          </section>

          <section className="card item-section" aria-labelledby="status-title">
            <h2 id="status-title" className="section-title">{t.itemPage.statusTitle}</h2>
            {targets.length ? (
              <form className="status-form" method="post" onSubmit={changeStatus}>
                <SelectField id="next-status" label={t.itemPage.changeStatus} value={nextStatus}
                  onChange={(e) => setNextStatus(e.target.value as ItemStatus | "")}>
                  <option value="">{t.common.notSet}</option>
                  {targets.map((s) => (
                    <option key={s} value={s}>{t.status[s]}</option>
                  ))}
                </SelectField>
                <button type="submit" className="btn btn-secondary" aria-busy={busy === "status"}>{t.itemPage.applyStatus}</button>
              </form>
            ) : (
              <p className="muted">{t.itemPage.statusLocked}</p>
            )}
            {history.length ? (
              <>
                <h3 className="subsection-title">{t.itemPage.historyTitle}</h3>
                <ol className="history">
                  {[...history].reverse().map((change, index) => (
                    <li key={`${change.changed_at}-${index}`}>
                      <span>
                        {change.from_status
                          ? fill(t.itemPage.historyChange, { from: t.status[change.from_status], to: t.status[change.to_status] })
                          : fill(t.itemPage.historyCreated, { to: t.status[change.to_status] })}
                      </span>
                      <time className="muted" dateTime={change.changed_at}>{formatMoment(change.changed_at, locale)}</time>
                    </li>
                  ))}
                </ol>
              </>
            ) : null}
          </section>

          <div className="danger-zone">
            <button type="button" className="btn btn-secondary" onClick={() => { setDialogError(""); setPending("deleteItem"); }}>
              {t.itemPage.deleteItem}
            </button>
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={pending !== null}
        title={pending === "deleteItem" ? t.itemPage.deleteTitle : t.itemPage.deletePhotoTitle}
        body={<p>{pending === "deleteItem" ? t.itemPage.deleteBody : t.itemPage.deletePhotoBody}</p>}
        confirmLabel={pending === "deleteItem" ? t.itemPage.deleteConfirm : t.itemPage.deletePhotoConfirm}
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
