/** An error answer from the DOLAB API, or a network failure (status 0). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** Seconds to wait before trying again, from a Retry-After header (429). */
  readonly retryAfter: number | null;

  constructor(status: number, detail: string, retryAfter: number | null = null) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

/** FastAPI sends {"detail": "text"} or, for validation errors, {"detail": [{"msg": ...}, ...]}. */
export function detailFrom(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
        .filter(Boolean)
        .join("; ");
    }
  }
  return "";
}

export function retryAfterFrom(headers: Headers): number | null {
  const value = headers.get("Retry-After");
  if (value === null) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}
