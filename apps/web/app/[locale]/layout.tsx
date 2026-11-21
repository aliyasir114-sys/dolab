import type { ReactNode } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AppProvider } from "../../components/app-context.tsx";
import { LOCALES, MESSAGES, dirOf, isLocale } from "../../lib/i18n/messages.ts";
import "../theme.css";
import "../app.css";

type Props = { children: ReactNode; params: Promise<{ locale: string }> };

export const dynamicParams = false;

export function generateStaticParams() {
  return LOCALES.map((locale) => ({ locale }));
}

export async function generateMetadata({ params }: Omit<Props, "children">): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: isLocale(locale) ? MESSAGES[locale].appName : "DOLAB",
    robots: { index: false, follow: false },
  };
}

// Each language is its own root layout, so <html lang dir> is right from the first byte.
export default async function LocaleLayout({ children, params }: Props) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return (
    <html lang={locale} dir={dirOf(locale)}>
      <body>
        <AppProvider locale={locale}>{children}</AppProvider>
      </body>
    </html>
  );
}
