import { useState } from "react";
import { api } from "../lib/api";
import { dateOnly, dateTimeET, money } from "../lib/format";
import { Badge, Drawer, Empty, PageHead, Seg, useAction, useApi } from "../components/ui";

export default function Receiving() {
  const [status, setStatus] = useState("");
  const { data, reload } = useApi<any[]>(`/v1/rdfi/entries${status ? `?status=${status}` : ""}`, [status]);
  const { data: accounts, reload: reloadAccts } = useApi<any[]>("/v1/rdfi/accounts");
  const [open, setOpen] = useState<string | null>(null);
  const [sim, setSim] = useState(false);
  const refresh = () => { reload(); reloadAccts(); };

  return (
    <>
      <PageHead title="Inbound entries"
        sub="Entries other banks send to our customers. Bad accounts are returned automatically; suspicious credits are posted with funds held until reviewed."
        actions={<button className="btn" onClick={() => setSim(true)}>Simulate an inbound file</button>} />
      <div className="filters">
        <Seg value={status} options={[["", "All"], ["held", "Held"], ["posted", "Posted"], ["return_pending,returned", "Returned"]]} onChange={setStatus} />
      </div>
      <section className="panel flush">
        <div className="table-wrap">
          {data && data.length ? (
            <table>
              <thead><tr><th>Received</th><th>From</th><th>SEC</th><th>Direction</th><th className="amt">Amount</th><th>Account</th><th>Name on entry</th><th>Funds available</th><th>Status</th></tr></thead>
              <tbody>
                {data.map((e) => (
                  <tr key={e.id} className="click" onClick={() => setOpen(e.id)} tabIndex={0} onKeyDown={(k) => k.key === "Enter" && setOpen(e.id)}>
                    <td className="nowrap">{dateTimeET(e.created_at)}</td>
                    <td>{e.company_name}<div className="muted small">{e.company_entry_description}</div></td>
                    <td><span className="chip">{e.sec_code}</span></td>
                    <td>{e.direction}</td>
                    <td className="amt">{money(e.amount_cents)}</td>
                    <td>ending {e.account_last4}</td>
                    <td>{e.individual_name || <span className="muted">—</span>}</td>
                    <td className="nowrap">{e.funds_available_at ? dateTimeET(e.funds_available_at) : <span className="muted">—</span>}</td>
                    <td><Badge s={e.status} />{e.return_code ? <span className="muted small"> {e.return_code}</span> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No inbound entries yet. In the sandbox, simulate a file to see the receiving flow.</Empty>}
        </div>
      </section>

      <section className="panel flush" style={{ marginTop: 16 }}>
        <h2>Customer accounts (core banking)</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Owner</th><th>Account</th><th>Type</th><th>Status</th><th>Debit controls</th><th className="amt">Ledger balance</th><th className="amt">Available</th></tr></thead>
            <tbody>
              {accounts?.map((a) => (
                <tr key={a.id}>
                  <td>{a.owner_name}<div className="muted small">{a.owner_type}, opened {dateOnly(a.opened_on)}</div></td>
                  <td className="mono">{a.account_number}</td>
                  <td>{a.account_type}</td>
                  <td><Badge s={a.status === "open" ? "settled" : "rejected"} label={a.status} /></td>
                  <td className="small">{a.debit_block ? "All debits blocked" : a.allowed_debit_company_ids.length ? `Only ${a.allowed_debit_company_ids.join(", ")}` : <span className="muted">None</span>}</td>
                  <td className="amt">{money(a.ledger_balance_cents)}</td>
                  <td className="amt">{money(a.available_balance_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {open && <InboundDrawer id={open} onClose={() => setOpen(null)} onChanged={refresh} />}
      {sim && <SimulateInbound accounts={accounts ?? []} onClose={() => setSim(false)} onDone={refresh} />}
    </>
  );
}

export function InboundDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { data: e, reload } = useApi<any>(`/v1/rdfi/entries/${id}`);
  const { run, busy } = useAction();
  const [note, setNote] = useState("");
  const [code, setCode] = useState("R10");
  const [wsud, setWsud] = useState(false);
  const done = () => { reload(); onChanged(); };
  if (!e) return <Drawer title="Inbound entry" onClose={onClose}><p className="muted">Loading…</p></Drawer>;
  return (
    <Drawer title={`${money(e.amount_cents)} ${e.direction} from ${e.company_name}`} sub={<Badge s={e.status} />} onClose={onClose}>
      <dl className="kv">
        <dt>SEC and description</dt><dd>{e.sec_code}, {e.company_entry_description}</dd>
        <dt>Company ID</dt><dd>{e.company_id}</dd>
        <dt>Account</dt><dd>ending {e.account_last4}</dd>
        <dt>Name on entry</dt><dd>{e.individual_name || "—"}</dd>
        <dt>Settlement</dt><dd>{dateOnly(e.settlement_date)} ({e.window})</dd>
        <dt>Funds available</dt><dd>{e.funds_available_at ? dateTimeET(e.funds_available_at) : "Held"}</dd>
        <dt>Trace</dt><dd className="mono">{e.trace_number}</dd>
        {e.return_code && (<><dt>Return</dt><dd>{e.return_code}</dd></>)}
      </dl>
      {e.screening.map((s: any, i: number) => (
        <div key={i} style={{ marginBottom: 12 }}>
          <strong style={{ textTransform: "capitalize" }}>{s.kind.replace("_", " ")}</strong> <Badge s={s.outcome === "clear" ? "settled" : "held"} label={`${s.outcome}${s.score ? `, score ${s.score}` : ""}`} />
          {s.reasons.length > 0 && <ul className="reasons">{s.reasons.map((r: string, j: number) => <li key={j}>{r}</li>)}</ul>}
        </div>
      ))}
      {e.nocs.length > 0 && <div className="note info">Notification of Change queued: {e.nocs.map((n: any) => `${n.change_code} → ${n.corrected_data}`).join(", ")}</div>}

      {e.status === "held" && (
        <section className="panel">
          <h3>Decide this hold</h3>
          <div className="field"><label htmlFor="n">Decision note</label><input id="n" value={note} onChange={(x) => setNote(x.target.value)} placeholder="e.g. Customer confirmed the refund by phone" /></div>
          <div className="row">
            <button className="btn primary" disabled={busy || !note} onClick={() => run(() => api(`/v1/rdfi/entries/${id}/decision`, { body: { decision: "release", note } }), "Funds released to the customer", done)}>Release funds</button>
            <button className="btn danger" disabled={busy || !note} onClick={() => run(() => api(`/v1/rdfi/entries/${id}/decision`, { body: { decision: "return", note } }), "Return queued", done)}>Return to sender</button>
          </div>
        </section>
      )}

      {e.status === "posted" && e.direction === "debit" && (
        <section className="panel">
          <h3>Customer says this debit wasn't authorized</h3>
          <p className="small muted">Consumer accounts: R05, R07, R10 or R11 within 60 days, with a signed Written Statement of Unauthorized Debit. Business accounts: R29 within 2 banking days.</p>
          <div className="row">
            <select value={code} onChange={(x) => setCode(x.target.value)} aria-label="Return code">{["R05", "R07", "R10", "R11", "R29"].map((c) => <option key={c}>{c}</option>)}</select>
            <label className="check"><input type="checkbox" checked={wsud} onChange={(x) => setWsud(x.target.checked)} /> WSUD signed</label>
            <button className="btn" disabled={busy} onClick={() => run(() => api(`/v1/rdfi/entries/${id}/unauthorized`, { body: { return_code: code, wsud_on_file: wsud } }), `Customer re-credited; ${code} queued`, done)}>Re-credit and return</button>
          </div>
        </section>
      )}
    </Drawer>
  );
}

function SimulateInbound({ accounts, onClose, onDone }: { accounts: any[]; onClose: () => void; onDone: () => void }) {
  const { run, busy } = useAction();
  const [rows, setRows] = useState([
    { account_number: accounts[0]?.account_number ?? "", amount: "1500.00", direction: "credit", individual_name: accounts[0]?.owner_name ?? "", company_name: "Payroll Co", sec_code: "PPD" },
  ]);
  const set = (i: number, k: string, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <Drawer title="Simulate an inbound file" sub="FedACH delivers these entries to our customers. Sandbox only." onClose={onClose}>
      <p className="small muted">Try a credit to a new account under a different name to see credit monitoring hold it, a closed account for R02, an unknown account for R03, or a debit to Harbor Logistics from an unapproved company for R29.</p>
      {rows.map((r, i) => (
        <div className="panel" key={i} style={{ marginBottom: 10 }}>
          <div className="grid cols-2">
            <div className="field"><label>Account</label>
              <input list="accts" value={r.account_number} onChange={(e) => set(i, "account_number", e.target.value)} />
            </div>
            <div className="field"><label>Amount (USD)</label><input value={r.amount} onChange={(e) => set(i, "amount", e.target.value)} /></div>
            <div className="field"><label>Direction</label>
              <select value={r.direction} onChange={(e) => set(i, "direction", e.target.value)}><option>credit</option><option>debit</option></select></div>
            <div className="field"><label>SEC code</label>
              <select value={r.sec_code} onChange={(e) => set(i, "sec_code", e.target.value)}>{["PPD", "CCD", "WEB", "TEL"].map((s) => <option key={s}>{s}</option>)}</select></div>
            <div className="field"><label>Name on entry</label><input value={r.individual_name} onChange={(e) => set(i, "individual_name", e.target.value)} /></div>
            <div className="field"><label>Sending company</label><input value={r.company_name} onChange={(e) => set(i, "company_name", e.target.value)} /></div>
          </div>
        </div>
      ))}
      <datalist id="accts">{accounts.map((a) => <option key={a.id} value={a.account_number}>{a.owner_name}</option>)}</datalist>
      <div className="row">
        <button className="btn" onClick={() => setRows([...rows, { ...rows[rows.length - 1] }])}>Add entry</button>
        <button className="btn primary" disabled={busy} onClick={() => run(
          () => api("/v1/sandbox/inbound", { body: { entries: rows.map((r) => ({ account_number: r.account_number, amount_cents: Math.round(parseFloat(r.amount) * 100), direction: r.direction, sec_code: r.sec_code, individual_name: r.individual_name, company_name: r.company_name, company_id: r.company_name === "Brightway Utilities" ? "2222222222" : "9876543210" })) } }),
          "Inbound file received and processed", () => { onDone(); onClose(); })}>Deliver file</button>
      </div>
    </Drawer>
  );
}
