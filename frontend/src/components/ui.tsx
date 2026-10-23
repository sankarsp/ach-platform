import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api } from "../lib/api";
import { statusLabel, tone } from "../lib/format";

export function Badge({ s, label }: { s: string; label?: string }) {
  return <span className={`badge ${tone(s)}`}>{label ?? statusLabel[s] ?? s.replace(/_/g, " ")}</span>;
}

export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    load();
  }, [load]);
  return { data, error, loading, reload: load };
}

type Toast = { id: number; text: string; bad?: boolean };
const ToastCtx = createContext<(text: string, bad?: boolean) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, bad = false) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, bad }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), bad ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.bad ? "bad" : ""}`}>{t.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/** Run an API action, toast the outcome, then refresh. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (fn: () => Promise<unknown>, ok: string, after?: () => void) => {
      setBusy(true);
      try {
        await fn();
        toast(ok);
        after?.();
        return true;
      } catch (e: any) {
        toast(e.message, true);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [toast],
  );
  return { run, busy };
}

export function Drawer({ title, sub, onClose, children }: { title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <header>
          <button className="btn sm close" onClick={onClose} aria-label="Close">Close</button>
          <h2 style={{ margin: 0 }}>{title}</h2>
          {sub && <div className="muted small">{sub}</div>}
        </header>
        <div className="body">{children}</div>
      </aside>
    </>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group">
      {options.map(([v, l]) => (
        <button key={v} className={v === value ? "on" : ""} onClick={() => onChange(v)} aria-pressed={v === value}>
          {l}
        </button>
      ))}
    </div>
  );
}
