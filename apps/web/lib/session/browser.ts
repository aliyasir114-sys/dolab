import { ApiError, detailFrom, retryAfterFrom } from "../api/errors.ts";
import type { TokenSource } from "../api/client.ts";
import type { AccessToken } from "../api/types.ts";

/**
 * The browser side of the session (plan 4.1). The access token lives in this object's memory
 * only: never in localStorage, sessionStorage, IndexedDB or a readable cookie (P10). The refresh
 * token is an HttpOnly cookie that only the web's /api/session/* routes can read.
 */
export type Session = TokenSource & {
  signIn(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  /** Restore the session after a page load, using the refresh cookie. */
  restore(): Promise<boolean>;
  isSignedIn(): boolean;
};

type Options = { fetch?: typeof fetch; base?: string };

export function createSession(options: Options = {}): Session {
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  const base = options.base ?? "/api/session";
  let token: string | null = null;
  let refreshing: Promise<string | null> | null = null;

  async function post(action: string, json?: unknown): Promise<Response> {
    try {
      return await doFetch(`${base}/${action}`, {
        method: "POST",
        headers: json === undefined ? {} : { "Content-Type": "application/json" },
        body: json === undefined ? undefined : JSON.stringify(json),
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "network");
    }
  }

  async function fail(response: Response): Promise<never> {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw new ApiError(response.status, detailFrom(body), retryAfterFrom(response.headers));
  }

  async function doRefresh(): Promise<string | null> {
    const response = await post("refresh");
    if (!response.ok) {
      token = null;
      return null;
    }
    token = ((await response.json()) as AccessToken).access_token;
    return token;
  }

  return {
    get: () => token,
    // Several requests can get a 401 at once: they share one refresh, so the refresh token is
    // used once (a reused refresh token would end the whole session family on the server).
    refresh() {
      refreshing ??= doRefresh().finally(() => {
        refreshing = null;
      });
      return refreshing;
    },
    async signIn(email, password) {
      const response = await post("login", { email, password });
      if (!response.ok) await fail(response);
      token = ((await response.json()) as AccessToken).access_token;
    },
    async signOut() {
      token = null;
      await post("logout").catch(() => undefined);
    },
    async restore() {
      return (await this.refresh()) !== null;
    },
    isSignedIn: () => token !== null,
  };
}
