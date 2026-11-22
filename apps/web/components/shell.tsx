"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useApp } from "./app-context.tsx";

function Logo() {
  return (
    <svg aria-hidden="true" width="48" height="24" viewBox="0 0 48 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
      <path d="M2 22L10 4l8 18M15 22L24 2l9 20M30 22l8-16 8 16M1 22h46" />
    </svg>
  );
}

/** The same page in the other language: /ar/x <-> /en/x. */
function otherLanguageHref(pathname: string, locale: string): string {
  const other = locale === "ar" ? "en" : "ar";
  const rest = pathname.split("/").slice(2).join("/");
  return `/${other}${rest ? `/${rest}` : ""}`;
}

export function Shell({ children, signedIn = false }: { children: ReactNode; signedIn?: boolean }) {
  const { t, locale, session } = useApp();
  const pathname = usePathname() ?? `/${locale}`;
  const router = useRouter();
  const wardrobe = `/${locale}/wardrobe`;

  async function signOut() {
    await session.signOut();
    router.replace(`/${locale}/sign-in`);
  }

  return (
    <div className="shell">
      <header className="app-header surface-dark">
        <Link className="app-brand" href={signedIn ? wardrobe : `/${locale}/sign-in`}>
          <Logo />
          <span>{t.appName}</span>
        </Link>
        <nav className="app-nav" aria-label={t.nav.main}>
          {signedIn
            ? ([["wardrobe", t.nav.wardrobe], ["dashboard", t.nav.dashboard], ["profile", t.nav.profile]] as const).map(([page, label]) => {
                const href = `/${locale}/${page}`;
                // The item pages belong to the wardrobe: marked as its section, not as the page.
                const current = pathname === href ? "page" : pathname.startsWith(`${href}/`) ? "true" : undefined;
                return (
                  <Link key={page} href={href} aria-current={current}>
                    {label}
                  </Link>
                );
              })
            : null}
          {signedIn ? (
            <button type="button" onClick={signOut}>
              {t.nav.signOut}
            </button>
          ) : null}
          <a
            className="lang-switch"
            href={otherLanguageHref(pathname, locale)}
            hrefLang={locale === "ar" ? "en" : "ar"}
            lang={locale === "ar" ? "en" : "ar"}
            aria-label={t.nav.otherLanguageName}
          >
            {t.nav.otherLanguage}
          </a>
        </nav>
      </header>
      <main className="page">
        <div className="page-inner">{children}</div>
      </main>
    </div>
  );
}
