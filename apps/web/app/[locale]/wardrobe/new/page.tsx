"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { useApp } from "../../../../components/app-context.tsx";
import { Shell } from "../../../../components/shell.tsx";
import { Field, FileField, FormError, SelectField, generalError, showErrors } from "../../../../components/ui.tsx";
import { useRequireSession } from "../../../../components/use-require-session.ts";
import { ApiError } from "../../../../lib/api/errors.ts";
import { createItem } from "../../../../lib/api/items.ts";
import { MAX_PHOTOS_PER_ITEM, PHOTO_TYPES } from "../../../../lib/api/photos.ts";
import { CREATABLE_STATUSES } from "../../../../lib/api/types.ts";
import { fill, plural, type Messages } from "../../../../lib/i18n/messages.ts";
import { checkPhotos, validateItem, type ItemFieldError, type ItemForm, type PhotoProblem } from "../../../../lib/validation/item.ts";

const EMPTY: ItemForm = { name: "", category: "", primaryColor: "", status: "available", purchaseDate: "", price: "", currency: "" };

function today(): string {
  return new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in the visitor's time zone
}

function fieldMessage(t: Messages, error: ItemFieldError): string {
  switch (error.code) {
    case "required":
      return t.common.required;
    case "tooLong":
      return fill(t.item.tooLong, { max: error.max });
    default:
      return t.item[error.code];
  }
}

function photoMessage(t: Messages, problem: PhotoProblem): string {
  return problem.code === "tooManyPhotos" ? t.item.tooManyPhotos : fill(t.item[problem.code], { name: problem.name });
}

export default function NewItemPage() {
  const { t, locale, api, photos } = useApp();
  const router = useRouter();
  const ready = useRequireSession();
  const form = useRef<HTMLFormElement>(null);
  const [values, setValues] = useState<ItemForm>(EMPTY);
  const [files, setFiles] = useState<File[]>([]);
  const [errors, setErrors] = useState<Partial<Record<keyof ItemForm | "photos", string>>>({});
  const [formError, setFormError] = useState("");
  const [savedWithProblems, setSavedWithProblems] = useState("");
  const [busy, setBusy] = useState(false);
  // A ref, not only state: two quick presses must not create the item twice.
  const sending = useRef(false);

  // Editing a field clears its error at once (Gemini's review of part 1).
  const set = (key: keyof ItemForm) => (event: { target: { value: string } }) => {
    setValues((v) => ({ ...v, [key]: event.target.value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (sending.current) return; // already sending
    setFormError("");
    const { errors: itemErrors, input } = validateItem(values, today());
    const photoProblems = checkPhotos(files);
    const found: typeof errors = {};
    for (const e of itemErrors) found[e.field] ??= fieldMessage(t, e);
    if (photoProblems.length) found.photos = photoProblems.map((p) => photoMessage(t, p)).join(" ");
    showErrors(form.current, () => setErrors(found));
    if (!input || photoProblems.length) return;

    sending.current = true;
    setBusy(true);
    try {
      const item = await createItem(api, input);
      let failed = 0;
      for (const file of files) {
        try {
          await photos.upload(item.id, file, file.name);
        } catch {
          failed += 1;
        }
      }
      if (failed === 0) {
        router.push(`/${locale}/wardrobe`);
        return;
      }
      setSavedWithProblems(fill(t.item.photosFailed, { count: failed }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) router.replace(`/${locale}/sign-in`);
      else setFormError(generalError(t, error));
    }
    sending.current = false;
    setBusy(false);
  }

  if (!ready) {
    return (
      <Shell signedIn>
        <p className="muted">{t.common.loading}</p>
      </Shell>
    );
  }

  return (
    <Shell signedIn>
      <div className="wide-form">
        <h1 className="page-title">{t.item.addTitle}</h1>
        <FormError message={formError} />
        <p className="form-status" role="status">{savedWithProblems}</p>
        {savedWithProblems ? (
          <Link className="btn btn-primary" href={`/${locale}/wardrobe`}>
            {t.wardrobe.title}
          </Link>
        ) : (
          <form ref={form} className="form-stack" method="post" onSubmit={submit} noValidate>
            <div className="form-grid">
              <Field id="name" label={t.item.name} placeholder={t.item.namePlaceholder} maxLength={120}
                value={values.name} onChange={set("name")} error={errors.name} required />
              <Field id="category" label={t.item.category} placeholder={t.item.categoryPlaceholder} maxLength={60}
                value={values.category} onChange={set("category")} error={errors.category} required />
              <Field id="primaryColor" label={t.item.color} optional={t.item.optional} placeholder={t.item.colorPlaceholder}
                maxLength={40} value={values.primaryColor} onChange={set("primaryColor")} error={errors.primaryColor} />
              <SelectField id="status" label={t.item.status} value={values.status} onChange={set("status")} error={errors.status}>
                {CREATABLE_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t.status[s]}
                  </option>
                ))}
              </SelectField>
              <Field id="purchaseDate" label={t.item.purchaseDate} optional={t.item.optional} type="date" max={today()}
                value={values.purchaseDate} onChange={set("purchaseDate")} error={errors.purchaseDate} />
              <div className="field-pair">
                <Field id="price" label={t.item.price} optional={t.item.optional} inputMode="decimal" dir="ltr"
                  value={values.price} onChange={set("price")} error={errors.price} />
                <Field id="currency" label={t.item.currency} optional={t.item.optional} placeholder={t.item.currencyPlaceholder}
                  maxLength={3} autoCapitalize="characters" value={values.currency} onChange={set("currency")}
                  error={errors.currency} />
              </div>
            </div>
            <FileField id="photos" label={t.item.photos} optional={t.item.optional} hint={t.item.photosHint}
              buttonLabel={t.item.choosePhotos}
              summary={files.length
                ? fill(t.item.photosChosen, { count: plural(t.item.photoCount, files.length, locale), names: files.map((f) => f.name).join(locale === "ar" ? "، " : ", ") })
                : t.item.noPhotosChosen}
              accept={PHOTO_TYPES.join(",")} multiple error={errors.photos}
              onChange={(e) => {
                setFiles(Array.from(e.target.files ?? []).slice(0, MAX_PHOTOS_PER_ITEM + 1));
                setErrors((x) => ({ ...x, photos: undefined }));
              }} />
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" aria-busy={busy}>
                {busy ? t.item.saving : t.item.save}
              </button>
              <Link className="btn btn-secondary" href={`/${locale}/wardrobe`}>
                {t.item.cancel}
              </Link>
            </div>
          </form>
        )}
      </div>
    </Shell>
  );
}
