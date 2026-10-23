import { useState } from "react";
import { api } from "../lib/api";
import { money, pct } from "../lib/format";
import { Badge, Drawer, PageHead, useAction, useApi } from "../components/ui";

function Rate({ value, limit }: { value: number; limit: number }) {
  const scale = limit * 2;
  return (
    <div title={`${pct(value)} against a ${pct(limit)} threshold`}>
      <div className={`meter ${value > limit ? "over" : ""}`}>
        <span style={{ width: `${Math.min(value / scale, 1) * 100}%` }} />
        <i style={{ left: "50%" }} />
      </div>
      <div className="small" style={{ marginTop: 2 }}>{pct(value)} <span className="muted">of {pct(limit)}</span></div>
    </div>
  );
}

export default function Originators() {
  const { data, reload } = useApi<any[]>("/v1/originators");
  const [edit, setEdit] = useState<any | null>(null);
  const [cpsFor, setCpsFor] = useState<any | null>(null);
  return (
    <>
      <PageHead title="Originators" sub="Companies approved to send entries through us, their limits, and their 60-day return rates against Nacha thresholds (debit entries)." />
      <section className="panel flush">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Originator</th><th>SEC codes</th><th>Status</th><th className="amt">Per entry</th><th className="amt">Daily limit</th><th>Unauthorized</th><th>Administrative</th><th>Overall</th><th></th></tr></thead>
            <tbody>
              {data?.map((o) => {
                const r = o.return_rates;
                return (
                  <tr key={o.id}>
                    <td>{o.name}<div className="muted small">Company ID {o.company_id}, {o.risk_rating} risk{o.is_third_party_sender ? ", Third-Party Sender" : ""}</div></td>
                    <td>{o.allowed_sec_codes.map((s: string) => <span key={s} className="chip" style={{ marginRight: 4 }}>{s}</span>)}<div className="muted small">{o.allows_debits ? "Credits and debits" : "Credits only"}{o.same_day_enabled ? ", Same Day" : ""}</div></td>
                    <td><Badge s={o.status === "active" ? "settled" : "held"} label={o.status} /></td>
                    <td className="amt">{money(o.per_entry_limit_cents)}</td>
                    <td className="amt">{money(o.daily_limit_cents)}</td>
                    <td><Rate value={r.unauthorized} limit={r.thresholds.unauthorized} /></td>
                    <td><Rate value={r.administrative} limit={r.thresholds.administrative} /></td>
                    <td><Rate value={r.overall} limit={r.thresholds.overall} /></td>
                    <td><div className="row"><button className="btn sm" onClick={() => setCpsFor(o)}>Receivers</button><button className="btn sm" onClick={() => setEdit(o)}>Edit limits</button></div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <p className="muted small" style={{ marginTop: 8 }}>The marker on each bar is the Nacha threshold: unauthorized 0.5%, administrative 3%, overall 15%. Rates are only meaningful once an originator has sent 20 or more debits.</p>
      {edit && <EditLimits o={edit} onClose={() => setEdit(null)} onDone={reload} />}
      {cpsFor && <Receivers o={cpsFor} onClose={() => setCpsFor(null)} />}
    </>
  );
}

function EditLimits({ o, onClose, onDone }: { o: any; onClose: () => void; onDone: () => void }) {
  const [per, setPer] = useState(String(o.per_entry_limit_cents / 100));
  const [daily, setDaily] = useState(String(o.daily_limit_cents / 100));
  const [status, setStatus] = useState(o.status);
  const { run, busy } = useAction();
  return (
    <Drawer title={`Limits for ${o.name}`} onClose={onClose}>
      <div className="field"><label htmlFor="per">Per-entry limit (USD)</label><input id="per" value={per} onChange={(e) => setPer(e.target.value)} /></div>
      <div className="field"><label htmlFor="daily">Daily exposure limit (USD)</label><input id="daily" value={daily} onChange={(e) => setDaily(e.target.value)} /><span className="hint">Payments that would exceed this are held for review.</span></div>
      <div className="field"><label htmlFor="st">Status</label><select id="st" value={status} onChange={(e) => setStatus(e.target.value)}><option value="active">Active</option><option value="suspended">Suspended</option></select></div>
      <button className="btn primary" disabled={busy} onClick={() => run(() => api(`/v1/originators/${o.id}`, { method: "PATCH", body: { per_entry_limit_cents: Math.round(+per * 100), daily_limit_cents: Math.round(+daily * 100), status } }), "Limits saved", () => { onDone(); onClose(); })}>Save limits</button>
    </Drawer>
  );
}

function Receivers({ o, onClose }: { o: any; onClose: () => void }) {
  const { data, reload } = useApi<any[]>(`/v1/counterparties?originator_id=${o.id}`);
  const { run, busy } = useAction();
  const [f, setF] = useState({ name: "", party_type: "consumer", routing_number: "", account_number: "", account_type: "checking" });
  return (
    <Drawer title={`Receivers for ${o.name}`} onClose={onClose}>
      <table>
        <thead><tr><th>Name</th><th>Account</th><th>Validation</th><th>Status</th></tr></thead>
        <tbody>{data?.map((c) => (
          <tr key={c.id}><td>{c.name}<div className="muted small">{c.party_type}{c.country !== "US" ? `, ${c.country}` : ""}</div></td>
            <td className="small">{c.routing_number}<br />ending {c.account_last4}</td>
            <td><Badge s={c.validation_status === "verified" ? "settled" : c.validation_status === "failed" ? "rejected" : "held"} label={c.validation_status.replace("_", " ")} /></td>
            <td>{c.status === "active" ? "Active" : <span title={c.block_reason}>Blocked</span>}</td></tr>))}
        </tbody>
      </table>
      <h3 style={{ marginTop: 20 }}>Add a receiver</h3>
      <div className="grid cols-2">
        <div className="field"><label>Name</label><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div className="field"><label>Type</label><select value={f.party_type} onChange={(e) => setF({ ...f, party_type: e.target.value })}><option value="consumer">Consumer</option><option value="business">Business</option></select></div>
        <div className="field"><label>Routing number</label><input value={f.routing_number} onChange={(e) => setF({ ...f, routing_number: e.target.value })} inputMode="numeric" /></div>
        <div className="field"><label>Account number</label><input value={f.account_number} onChange={(e) => setF({ ...f, account_number: e.target.value })} /></div>
      </div>
      <button className="btn primary" disabled={busy || !f.name || !f.routing_number || !f.account_number}
        onClick={() => run(() => api("/v1/counterparties", { body: { ...f, originator_id: o.id, validate_with: "instant" } }), "Receiver added and validated", () => { reload(); setF({ ...f, name: "", account_number: "" }); })}>Add receiver</button>
    </Drawer>
  );
}
