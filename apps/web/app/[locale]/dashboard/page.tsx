"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useApp } from "../../../components/app-context.tsx";
import { Shell } from "../../../components/shell.tsx";
import { FormError, generalError } from "../../../components/ui.tsx";
import { useRequireSession } from "../../../components/use-require-session.ts";
import { getDashboard } from "../../../lib/api/account.ts";
import { ApiError } from "../../../lib/api/errors.ts";
import { ITEM_STATUSES, type Dashboard } from "../../../lib/api/types.ts";
import { fill, plural } from "../../../lib/i18n/messages.ts";

// The wardrobe's own statuses always show; market and donation counts only once they exist.
const ALWAYS = new Set(["available", "laundry", "repair", "stored", "lost"]);

export default function DashboardPage() {
  const { t, locale, api } = useApp();
  const router = useRouter();
  const ready = useRequireSession();
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      setData(await getDashboard(api));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace(`/${locale}/sign-in`);
      else setError(generalError(t, e));
    }
  }, [api, locale, router, t]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  if (!ready || !data) {
    return (
      <Shell signedIn>
        <FormError message={error} />
        {error ? (
          <button type="button" className="btn btn-secondary" onClick={() => void load()}>{t.common.retry}</button>
        ) : (
          <p className="muted">{t.common.loading}</p>
        )}
      </Shell>
    );
  }

  const percent = Math.round((data.usage_ratio ?? 0) * 100);
  const statuses = ITEM_STATUSES.filter((s) => ALWAYS.has(s) || (data.by_status[s] ?? 0) > 0);
  const colors = (Object.entries(data.colors) as Array<[string, number]>).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], locale));
  const most = colors[0]?.[1] ?? 1;

  return (
    <Shell signedIn>
      <div className="dashboard">
        <div>
          <p className="muted">{t.dashboard.caption}</p>
          <h1 className="page-title">{t.dashboard.title}</h1>
        </div>

        <section className="usage-card surface-dark" aria-labelledby="usage-title">
          <h2 id="usage-title" className="usage-title">{t.dashboard.usage}</h2>
          {data.active_items ? (
            <>
              <p className="usage-text">
                {fill(plural(t.dashboard.usageText, data.active_items, locale), { worn: data.worn_this_month })}
              </p>
              <div className="usage-bar" role="img" aria-label={fill(t.dashboard.usageBar, { percent })}>
                <span style={{ inlineSize: `${percent}%` }} />
              </div>
              <p className="usage-percent" aria-hidden="true">{percent}%</p>
            </>
          ) : (
            <p className="usage-text">{t.dashboard.noActive}</p>
          )}
        </section>

        <section aria-labelledby="status-title" className="dashboard-section">
          <h2 id="status-title" className="section-title">{t.dashboard.byStatus}</h2>
          <p className="muted">
            {fill(t.dashboard.totals, { items: plural(t.wardrobe.itemCount, data.total_items, locale), active: data.active_items })}
          </p>
          <ul className="stat-grid">
            {statuses.map((s) => (
              <li key={s} className="card stat-card">
                <span className="stat-label">{t.status[s]}</span>
                <span className="stat-value">{data.by_status[s] ?? 0}</span>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="colors-title" className="dashboard-section">
          <h2 id="colors-title" className="section-title">{t.dashboard.colors}</h2>
          {colors.length ? (
            <ul className="color-list">
              {colors.map(([name, count]) => (
                <li key={name}>
                  <span className="color-name">{name}</span>
                  <span className="color-bar" aria-hidden="true"><span style={{ inlineSize: `${(count / most) * 100}%` }} /></span>
                  <span className="color-count">{count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t.dashboard.noColors}</p>
          )}
        </section>
      </div>
    </Shell>
  );
}
