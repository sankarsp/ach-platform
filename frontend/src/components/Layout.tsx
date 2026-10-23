import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { api, getSession, setSession } from "../lib/api";

type Counts = { held_payments: number; open_alerts: number; held_inbound: number; open_incidents: number; p1_incidents: number; environment: string };

export function Layout() {
  const nav = useNavigate();
  const s = getSession();
  const [c, setC] = useState<Counts | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api<Counts>("/v1/ops/dashboard").then((d) => alive && setC(d)).catch(() => {});
    load();
    const t = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const n = (v?: number) => (v ? <span className="count">{v}</span> : null);
  return (
    <div className="shell">
      <nav className="side" aria-label="Main">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#2a3547" /><path d="M8 21h16M8 16h11M8 11h16" stroke="#33b5b3" strokeWidth="3" strokeLinecap="round" /></svg>
          ACH Operations
        </div>
        <NavLink to="/" end>Today</NavLink>
        <NavLink to="/review">Review queue {n((c?.open_alerts ?? 0))}</NavLink>
        <div className="group">Originating</div>
        <NavLink to="/payments" end>Payments {n(c?.held_payments)}</NavLink>
        <NavLink to="/payments/new">New payment</NavLink>
        <NavLink to="/originators">Originators</NavLink>
        <div className="group">Receiving</div>
        <NavLink to="/receiving">Inbound entries {n(c?.held_inbound)}</NavLink>
        <NavLink to="/returns">Returns and NOCs</NavLink>
        <div className="group">Control</div>
        <NavLink to="/files">Files and ledger</NavLink>
        <NavLink to="/calendar">Calendar and rules</NavLink>
        <NavLink to="/incidents">Incidents {n(c?.open_incidents)}</NavLink>
        <NavLink to="/audit">Audit log</NavLink>
        <div className="foot">
          Signed in as {s?.actor}
          <br />
          {c && c.environment !== "production" && <span className="env">Sandbox: simulated FedACH</span>}
          <br />
          <button onClick={() => { setSession(null); nav("/login"); }}>Sign out</button>
        </div>
      </nav>
      <main>
        <Outlet />
      </main>
    </div>
  );
}
