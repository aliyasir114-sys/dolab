"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { createApiClient, type ApiClient } from "../lib/api/client.ts";
import { serverPhotoStore, type PhotoStore } from "../lib/api/photos.ts";
import { createSession, type Session } from "../lib/session/browser.ts";
import { MESSAGES, type AppLocale, type Messages } from "../lib/i18n/messages.ts";

export type AppContext = {
  locale: AppLocale;
  t: Messages;
  session: Session;
  api: ApiClient;
  photos: PhotoStore;
};

const Context = createContext<AppContext | null>(null);

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/v1";

// One session per browser tab. On the server each render gets its own (never signed in), so no
// token can be shared between visitors.
let browserSession: Session | null = null;
function session(): Session {
  if (typeof window === "undefined") return createSession();
  browserSession ??= createSession();
  return browserSession;
}

export function AppProvider({ locale, children }: { locale: AppLocale; children: ReactNode }) {
  const value = useMemo<AppContext>(() => {
    const s = session();
    const api = createApiClient({ baseUrl: API_URL, tokens: s });
    return { locale, t: MESSAGES[locale], session: s, api, photos: serverPhotoStore(api) };
  }, [locale]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useApp(): AppContext {
  const value = useContext(Context);
  if (!value) throw new Error("useApp outside AppProvider");
  return value;
}
