const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

export type Session = { key: string; actor: string };

const STORE = "ach.session";

export function getSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORE);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function setSession(s: Session | null) {
  try {
    if (s) sessionStorage.setItem(STORE, JSON.stringify(s));
    else sessionStorage.removeItem(STORE);
  } catch {
    /* storage unavailable: session lives only in memory for this tab */
  }
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
  const s = getSession();
  const res = await fetch(`${BASE}${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers: {
      "Content-Type": "application/json",
      ...(s ? { Authorization: `Bearer ${s.key}`, "X-Actor": s.actor } : {}),
      ...opts.headers,
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const text = await res.text();
  const data = text && res.headers.get("content-type")?.includes("json") ? JSON.parse(text) : text;
  if (!res.ok) {
    const err = (data && (data as any).error) || {};
    const detail = Array.isArray((data as any)?.detail)
      ? (data as any).detail.map((d: any) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ")
      : undefined;
    throw new ApiError(res.status, err.code ?? "error", err.message ?? detail ?? `Request failed (${res.status})`);
  }
  return data as T;
}

export const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
