import { useState } from "react";
import { api } from "../lib/api";
import { dateOnly, dateTimeET, money, statusLabel } from "../lib/format";
import { Badge, Drawer, useAction, useApi } from "./ui";

const RETURN_CODES = ["R01", "R02", "R03", "R04", "R07", "R08", "R10", "R16", "R29"];
const NOC_CODES: [string, string][] = [["C01", "New account number"], ["C02", "New routing number"], ["C05", "Transaction code, e.g. 32"], ["C09", "Individual ID"]];

export function PaymentDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { data: p, reload } = useApi<any>(`/v1/payments/${id}`);
  const { run, busy } = useAction();
  const [note, setNote] = useState("");
  const [ret, setRet] = useState("R01");
  const [noc, setNoc] = useState("C01");
  const [nocData, setNocData] = useState("");
  const done = () => { reload(); onChanged(); };
  if (!p) return <Drawer title="Payment" onClose={onClose}><p className="muted">Loading…</p></Drawer>;

  const sanctions = (p.alerts ?? []).some((a: any) => a.kind === "sanctions" && a.status !== "closed");
  return (
    <Drawer
      title={`${money(p.amount_cents)} ${p.direction} to ${p.counterparty.name}`}
      sub={<>{p.originator.name} <Badge s={p.status} /></>}
      onClose={onClose}
    >
      {p.status_reason && <div className={`note ${p.status === "held" || p.status === "returned" ? "" : "info"}`}>{p.status_reason}</div>}

      <dl className="kv">
        <dt>SEC code and purpose</dt><dd>{p.sec_code}, {p.purpose.replace(/_/g, " ")}</dd>
        <dt>Company entry description</dt><dd><span className="chip">{p.company_entry_description}</span></dd>
        <dt>Receiver account</dt><dd>{p.counterparty.routing_number} / ending {p.counterparty.account_last4} ({p.counterparty.party_type})</dd>
        <dt>Requested effective date</dt><dd>{dateOnly(p.requested_effective_date)}</dd>
        <dt>Settlement</dt>
        <dd>
          {dateOnly(p.settlement_date)} at {dateTimeET(p.expected_settlement_at).split(", ").pop()} ET
          {p.same_day ? <> <Badge s="info" label="Same Day" /></> : null}
        </dd>
        <dt>Window and cut-off</dt><dd>{p.window_code}, release at {dateTimeET(p.window_cutoff_at)} ET</dd>
        <dt>Trace number</dt><dd>{p.trace_number ? <span className="mono">{p.trace_number}</span> : <span className="muted">Assigned at release</span>}</dd>
        {p.return_code && (<><dt>Return</dt><dd><Badge s="returned" label={p.return_code} /></dd></>)}
        <dt>Fraud score</dt><dd>{p.risk_score} / 100</dd>
      </dl>

      {p.plan_notes?.length > 0 && (
        <>
          <h3>Scheduling notes</h3>
          <ul className="reasons" style={{ marginBottom: 16 }}>{p.plan_notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>
        </>
      )}

      {p.status === "held" && (
        <section className="panel" style={{ marginBottom: 16 }}>
          <h3>Decide this hold</h3>
          {sanctions && <p className="small">Possible sanctions match. To release, record why it is a false positive and include the words "OFAC cleared".</p>}
          <div className="field">
            <label htmlFor="note">Decision note</label>
            <input id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Called receiver on file number; confirmed new account" />
          </div>
          <div className="row">
            <button className="btn primary" disabled={busy || !note} onClick={() => run(() => api(`/v1/payments/${id}/decision`, { body: { decision: "approve", note } }), "Payment released to the next window", done)}>Release payment</button>
            <button className="btn danger" disabled={busy || !note} onClick={() => run(() => api(`/v1/payments/${id}/decision`, { body: { decision: "reject", note } }), "Payment rejected", done)}>Reject payment</button>
          </div>
        </section>
      )}

      {p.screening?.length > 0 && (
        <>
          <h3>Screening</h3>
          {p.screening.map((s: any, i: number) => (
            <div key={i} style={{ marginBottom: 10 }}>
              <div className="row"><strong style={{ textTransform: "capitalize" }}>{s.kind.replace("_", " ")}</strong><Badge s={s.outcome === "clear" ? "settled" : s.outcome === "block" ? "rejected" : "held"} label={s.outcome} />{s.kind === "fraud" && <span className="muted small">score {s.score}</span>}</div>
              {s.reasons.length > 0 && <ul className="reasons">{s.reasons.map((r: string, j: number) => <li key={j}>{r}</li>)}</ul>}
            </div>
          ))}
        </>
      )}

      <h3 style={{ marginTop: 16 }}>History</h3>
      <ol className="timeline">
        {p.events.map((e: any, i: number) => (
          <li key={i}>
            <div><strong>{statusLabel[e.to] ?? e.to}</strong> <span className="muted small">{dateTimeET(e.at)} · {e.actor}</span></div>
            {e.detail?.reason && <div className="small">{e.detail.reason}</div>}
          </li>
        ))}
      </ol>

      <div className="row">
        {["held", "warehoused"].includes(p.status) && (
          <button className="btn danger" disabled={busy} onClick={() => run(() => api(`/v1/payments/${id}/cancel`, { body: {} }), "Payment cancelled", done)}>Cancel payment</button>
        )}
        {p.status === "settled" && (
          <button className="btn" disabled={busy} onClick={() => run(() => api(`/v1/payments/${id}/reversal`, { body: { reason: "duplicate" } }), "Reversal created", done)}>Reverse as duplicate</button>
        )}
      </div>

      {["transmitted", "settled"].includes(p.status) && (
        <section className="panel" style={{ marginTop: 16 }}>
          <h3>Sandbox: simulate the receiving bank</h3>
          <div className="row" style={{ marginBottom: 10 }}>
            <select value={ret} onChange={(e) => setRet(e.target.value)} aria-label="Return code">{RETURN_CODES.map((c) => <option key={c}>{c}</option>)}</select>
            <button className="btn" disabled={busy} onClick={() => run(() => api("/v1/sandbox/returns", { body: { payment_id: id, return_code: ret } }), `Return ${ret} received`, done)}>Send return</button>
          </div>
          <div className="row">
            <select value={noc} onChange={(e) => setNoc(e.target.value)} aria-label="Change code">{NOC_CODES.map(([c, l]) => <option key={c} value={c}>{c} {l}</option>)}</select>
            <input value={nocData} onChange={(e) => setNocData(e.target.value)} placeholder="Corrected data" style={{ width: 150 }} />
            <button className="btn" disabled={busy || !nocData} onClick={() => run(() => api("/v1/sandbox/nocs", { body: { payment_id: id, change_code: noc, corrected_data: nocData } }), `NOC ${noc} applied`, done)}>Send NOC</button>
          </div>
        </section>
      )}
    </Drawer>
  );
}
