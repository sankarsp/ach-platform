import { useState } from "react";
import { api } from "../lib/api";
import { dateOnly, dateTimeET, money } from "../lib/format";
import { Badge, Drawer, Empty, PageHead, Seg, useAction, useApi } from "../components/ui";

export default function Files() {
  const [tab, setTab] = useState<"files" | "recon" | "ledger">("files");
  const { data: files, reload } = useApi<any[]>("/v1/files?limit=200");
  const { data: recon } = useApi<any[]>("/v1/settlements");
  const { data: tb } = useApi<any>("/v1/ledger/trial-balance");
  const [raw, setRaw] = useState<any | null>(null);
  const [upload, setUpload] = useState(false);

  const openRaw = async (f: any) => setRaw({ ...f, text: await api<string>(`/v1/files/${f.id}/raw`) });

  return (
    <>
      <PageHead title="Files and ledger" sub="Every file exchanged with FedACH, the three-way settlement reconciliation, and the double-entry ledger."
        actions={<button className="btn" onClick={() => setUpload(true)}>Load a file from FedACH</button>} />
      <div className="filters">
        <Seg value={tab} options={[["files", "Files"], ["recon", "Reconciliation"], ["ledger", "Trial balance"]]} onChange={setTab} />
      </div>
      <section className="panel flush">
        <div className="table-wrap">
          {tab === "files" && (files?.length ? (
            <table>
              <thead><tr><th>Created</th><th>Direction</th><th>Modifier</th><th>Window</th><th className="amt">Entries</th><th className="amt">Debits</th><th className="amt">Credits</th><th>Settles</th><th>Status</th><th></th></tr></thead>
              <tbody>{files.map((f) => (
                <tr key={f.id}>
                  <td className="nowrap">{dateTimeET(f.created_at)}</td>
                  <td>{f.direction === "outbound" ? (f.kind === "returns" ? "Returns to FedACH" : "Forward to FedACH") : "From FedACH"}</td>
                  <td><span className="chip">{f.file_id_modifier}</span></td>
                  <td>{f.window ?? "—"}</td>
                  <td className="amt">{f.entry_count}</td>
                  <td className="amt">{money(f.total_debit_cents)}</td>
                  <td className="amt">{money(f.total_credit_cents)}</td>
                  <td className="nowrap">{dateOnly(f.settlement_date)}</td>
                  <td><Badge s={f.status} />{f.reconciliation && <> <Badge s={f.reconciliation} /></>}</td>
                  <td><button className="btn sm" onClick={() => openRaw(f)}>View file</button></td>
                </tr>))}
              </tbody>
            </table>
          ) : <Empty>No files yet.</Empty>)}

          {tab === "recon" && (recon?.length ? (
            <table>
              <thead><tr><th>Run</th><th>Window</th><th>Settlement date</th><th className="amt">Fed advice (net)</th><th className="amt">Ledger (net)</th><th className="amt">Core postings (net)</th><th>Result</th></tr></thead>
              <tbody>{recon.map((r) => (
                <tr key={r.id}>
                  <td className="nowrap">{dateTimeET(r.at)}</td><td>{r.window}</td><td>{dateOnly(r.settlement_date)}</td>
                  <td className="amt">{money(r.advice_net_cents)}</td><td className="amt">{money(r.ledger_net_cents)}</td>
                  <td className="amt">{money(r.core_net_cents)}</td><td><Badge s={r.status} /></td>
                </tr>))}
              </tbody>
            </table>
          ) : <Empty>Reconciliation runs after each window settles.</Empty>)}

          {tab === "ledger" && tb && (
            <>
              <div style={{ padding: "14px 18px 0" }}>
                {tb.balanced ? <div className="note good">Debits equal credits: {money(tb.total_debits)}.</div>
                  : <div className="note bad">Out of balance: debits {money(tb.total_debits)}, credits {money(tb.total_credits)}.</div>}
              </div>
              <table>
                <thead><tr><th>Account</th><th>Type</th><th className="amt">Debits</th><th className="amt">Credits</th><th className="amt">Balance</th></tr></thead>
                <tbody>{tb.accounts.map((a: any) => (
                  <tr key={a.code}><td>{a.name}<div className="muted small mono">{a.code}</div></td><td>{a.type}</td>
                    <td className="amt">{money(a.debits)}</td><td className="amt">{money(a.credits)}</td><td className="amt">{money(a.balance)}</td></tr>))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </section>

      {raw && (
        <Drawer title={`File ${raw.file_id_modifier}, ${raw.entry_count} entries`} sub={`SHA-256 ${raw.sha256.slice(0, 16)}…`} onClose={() => setRaw(null)}>
          {raw.errors?.length > 0 && <div className="note bad">{raw.errors.join("; ")}</div>}
          <pre className="raw">{raw.text}</pre>
        </Drawer>
      )}
      {upload && <UploadFile onClose={() => setUpload(false)} onDone={reload} />}
    </>
  );
}

function UploadFile({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [check, setCheck] = useState<any>(null);
  const { run, busy } = useAction();
  return (
    <Drawer title="Load a file from FedACH" sub="Forward entries go to receiving; returns and NOCs update the payments we sent." onClose={onClose}>
      <div className="field">
        <label htmlFor="f">Nacha file contents</label>
        <textarea id="f" rows={14} value={text} onChange={(e) => { setText(e.target.value); setCheck(null); }} />
        <input type="file" accept=".ach,.txt,.nacha" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()); }} />
      </div>
      {check && (check.valid ? <div className="note good">Valid: {check.batches} batches, {check.entries} entries, debits {money(check.total_debit_cents)}, credits {money(check.total_credit_cents)}.</div>
        : <div className="note bad">{check.errors.join("; ")}</div>)}
      <div className="row">
        <button className="btn" disabled={!text} onClick={async () => setCheck(await api("/v1/files/validate", { body: { content: text } }))}>Check file</button>
        <button className="btn primary" disabled={busy || !text || (check && !check.valid)} onClick={() => run(() => api("/v1/files/inbound", { body: { content: text } }), "File processed", () => { onDone(); onClose(); })}>Process file</button>
      </div>
    </Drawer>
  );
}
