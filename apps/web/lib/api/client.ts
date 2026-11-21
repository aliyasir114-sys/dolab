import { ApiError, detailFrom, retryAfterFrom } from "./errors.ts";

/**
 * The one place the web app talks to the DOLAB API (Ali, 2026-11-21: one layer that is easy to
 * replace). Pages and components call the functions in lib/api/*.ts, never fetch() directly.
 */
export type TokenSource = {
  /** The current access token, kept in memory only. */
  get(): string | null;
  /** Get a new access token through the session route; null if the session is over. */
  refresh(): Promise<string | null>;
};

export type ApiClientOptions = {
  /** e.g. https://api.dolab.example/v1 */
  baseUrl: string;
  tokens: TokenSource;
  fetch?: typeof fetch;
};

export type RequestOptions = {
  query?: Record<string, string | number | undefined>;
  json?: unknown;
  form?: FormData;
  /** Send the access token (default true). */
  auth?: boolean;
};

export type ApiResponse<T> = { data: T; headers: Headers };

export type ApiClient = {
  request<T>(method: string, path: string, options?: RequestOptions): Promise<ApiResponse<T>>;
  /** Absolute address for a relative path the API returned, such as a signed photo link. */
  mediaUrl(path: string): string;
};

export function createApiClient(options: ApiClientOptions): ApiClient {
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  const base = options.baseUrl.replace(/\/+$/, "");
  const origin = new URL(base).origin;

  function url(path: string, query?: RequestOptions["query"]): string {
    const target = new URL(base + path);
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined && value !== "") target.searchParams.set(key, String(value));
    }
    return target.toString();
  }

  async function send(method: string, path: string, opts: RequestOptions, token: string | null): Promise<Response> {
    const headers = new Headers({ Accept: "application/json" });
    let body: BodyInit | undefined;
    if (opts.json !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(opts.json);
    } else if (opts.form) {
      body = opts.form; // the browser sets the multipart boundary
    }
    if (token) headers.set("Authorization", `Bearer ${token}`);
    try {
      return await doFetch(url(path, opts.query), { method, headers, body, credentials: "omit", cache: "no-store" });
    } catch {
      throw new ApiError(0, "network");
    }
  }

  async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<ApiResponse<T>> {
    const auth = opts.auth ?? true;
    let response = await send(method, path, opts, auth ? options.tokens.get() : null);
    if (response.status === 401 && auth) {
      const fresh = await options.tokens.refresh();
      if (fresh) response = await send(method, path, opts, fresh);
    }
    if (!response.ok) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      throw new ApiError(response.status, detailFrom(body), retryAfterFrom(response.headers));
    }
    const data = response.status === 204 ? undefined : await response.json();
    return { data: data as T, headers: response.headers };
  }

  function mediaUrl(path: string): string {
    return new URL(path, origin).toString();
  }

  return { request, mediaUrl };
}
