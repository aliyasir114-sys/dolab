/** A scripted fetch: each call takes the next answer and records what was sent. */
export type Sent = { url: string; method: string; headers: Headers; body: unknown };

export function fakeFetch(answers: Array<(sent: Sent) => Response>) {
  const sent: Sent[] = [];
  const fn = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    let body: unknown = init.body ?? null;
    if (typeof body === "string" && headers.get("Content-Type") === "application/json") body = JSON.parse(body);
    const call: Sent = { url: String(input), method: init.method ?? "GET", headers, body };
    sent.push(call);
    const next = answers.shift();
    if (!next) throw new Error(`unexpected request ${call.method} ${call.url}`);
    return next(call);
  }) as typeof fetch;
  return { fn, sent };
}

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
