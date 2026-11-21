"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useApp } from "./app-context.tsx";

/** For signed-in pages: restore the session from the refresh cookie, or go to sign-in. */
export function useRequireSession(): boolean {
  const { session, locale } = useApp();
  const router = useRouter();
  const [ready, setReady] = useState(session.isSignedIn());

  useEffect(() => {
    if (ready) return;
    let cancelled = false;
    session.restore().then((ok) => {
      if (cancelled) return;
      if (ok) setReady(true);
      else router.replace(`/${locale}/sign-in?next=${encodeURIComponent(window.location.pathname)}`);
    });
    return () => {
      cancelled = true;
    };
  }, [ready, session, locale, router]);

  return ready;
}
