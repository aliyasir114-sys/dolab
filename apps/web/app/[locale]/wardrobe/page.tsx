"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useApp } from "../../../components/app-context.tsx";
import { Shell } from "../../../components/shell.tsx";
import { FormError, generalError } from "../../../components/ui.tsx";
import { useRequireSession } from "../../../components/use-require-session.ts";
import { ApiError } from "../../../lib/api/errors.ts";
import { listItems } from "../../../lib/api/items.ts";
import type { Item, ItemStatus } from "../../../lib/api/types.ts";
import { fill, plural } from "../../../lib/i18n/messages.ts";

// The pilot shows the wardrobe's own statuses; for_sale (market) and donated (hidden) come later.
const FILTERS: Array<ItemStatus | "all"> = ["all", "available", "laundry", "repair", "stored", "lost"];
const PAGE = 48;

export default function WardrobePage() {
  const { t, locale, api } = useApp();
  const router = useRouter();
  const ready = useRequireSession();
  const [filter, setFilter] = useState<ItemStatus | "all">("all");
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<{ total: number; available: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace(`/${locale}/sign-in`);
      else setError(generalError(t, e));
    },
    [locale, router, t],
  );

  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      setError("");
      try {
        const page = await listItems(api, { status: filter === "all" ? undefined : filter, limit: PAGE, offset });
        setItems((previous) => (offset === 0 ? page.items : [...previous, ...page.items]));
        setTotal(page.total);
      } catch (e) {
        fail(e);
      }
      setLoading(false);
    },
    [api, filter, fail],
  );

  useEffect(() => {
    if (ready) void load(0);
  }, [ready, load]);

  useEffect(() => {
    if (!ready) return;
    Promise.all([listItems(api, { limit: 1 }), listItems(api, { status: "available", limit: 1 })])
      .then(([all, available]) => setSummary({ total: all.total, available: available.total }))
      .catch(() => setSummary(null));
  }, [ready, api]);

  return (
    <Shell signedIn>
      <div className="wardrobe">
        <aside className="wardrobe-aside">
          <h1 className="page-title">{t.wardrobe.title}</h1>
          <p className="muted" aria-live="polite">
            {summary ? fill(t.wardrobe.summary, { items: plural(t.wardrobe.itemCount, summary.total, locale), available: summary.available }) : ""}
          </p>
          <div className="filter-group" role="group" aria-label={t.wardrobe.statusGroup}>
            {FILTERS.map((value) => (
              <button key={value} type="button" className="filter-btn" aria-pressed={filter === value}
                onClick={() => setFilter(value)}>
                {value === "all" ? t.wardrobe.all : t.status[value]}
              </button>
            ))}
          </div>
          <Link className="btn btn-primary" href={`/${locale}/wardrobe/new`}>
            {t.wardrobe.add}
          </Link>
        </aside>

        <section className="wardrobe-main" aria-busy={loading}>
          <FormError message={error} />
          {error ? (
            <button type="button" className="btn btn-secondary" onClick={() => load(0)}>
              {t.common.retry}
            </button>
          ) : null}
          {!loading && !error && items.length === 0 ? (
            <div className="card empty-state">
              <p>{filter === "all" ? t.wardrobe.empty : t.wardrobe.emptyFilter}</p>
              {filter === "all" ? (
                <Link className="btn btn-primary" href={`/${locale}/wardrobe/new`}>
                  {t.wardrobe.addFirst}
                </Link>
              ) : null}
            </div>
          ) : null}
          <ul className="item-grid">
            {items.map((item) => (
              <li key={item.id}>
                <Link className="item-link" href={`/${locale}/wardrobe/${encodeURIComponent(item.id)}`}>
                <article className="card item-card">
                  <div className="item-photo">
                    {item.cover_thumb_url ? (
                      // Signed links from the API, shown as they are (no image optimisation).
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={api.mediaUrl(item.cover_thumb_url)} alt="" loading="lazy" />
                    ) : (
                      <span>{t.wardrobe.noPhoto}</span>
                    )}
                  </div>
                  <div className="item-body">
                    <h2 className="item-name">{item.name}</h2>
                    <span className="status-pill" data-status={item.status}>
                      {t.status[item.status]}
                    </span>
                  </div>
                </article>
                </Link>
              </li>
            ))}
          </ul>
          {loading ? <p className="muted">{t.common.loading}</p> : null}
          {!loading && items.length < total ? (
            <button type="button" className="btn btn-secondary" onClick={() => load(items.length)}>
              {t.wardrobe.more}
            </button>
          ) : null}
        </section>
      </div>
    </Shell>
  );
}
