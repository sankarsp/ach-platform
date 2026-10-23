import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { dateOnly, dateTimeET, money } from "../lib/format";
import { WindowRail } from "../components/WindowRail";
import { Badge, Empty, PageHead, useAction, useApi } from "../components/ui";

export default function Today() {
  const { data: d, reload } = useApi<any>("/v1/ops/dashboard");
  const { data: files, reload: reloadFiles } = useApi<any[]>("/v1/files?limit=8");
  const { run, busy } = useAction();
  const refresh = () => { reload(); reloadFiles(); };
  if (!d) return <Empty>Loading today's position…</Empty>;

  const sandbox = d.environment !== "production";
  const credits = d.settling_today?.credit ?? { count: 0, amount_cents: 0 };
  const debits = d.settling_today?.debit ?? { count: 0, amount_cents: 0 };

  return (
    <>
      <PageHead
        title={dateOnly(d.today)}
        sub={d.is_banking_day ? "Entries release automatically at each cut-off. Clear the review queue before the next one." : `The Fed is closed today. Next banking day: ${dateOnly(d.next_banking_day)}.`}
        actions={
          <>
            <button className="btn" disabled={busy} onClick={() => run(() => api("/v1/ops/run", { body: {} }), "Ran the release, settlement and monitoring cycle", refresh)}>Run cycle now</button>
            {sandbox && (
              <button className="btn primary" disabled={busy}
                onClick={() => run(() => api("/v1/ops/run", { body: { force: true } }), "Released all queued entries and settled them (sandbox)", refresh)}>
                Release and settle all (sandbox)
              </button>
            )}
          </>
        }
      />

      <WindowRail windows={d.windows} queued={d.queued_by_window} banking={d.is_banking_day} holiday={d.holiday} />

      <div className="figures">
        <Fig k="Payments on hold" v={d.held_payments} warn={d.held_payments > 0} to="/payments?status=held" />
        <Fig k="Open alerts" v={d.open_alerts} warn={d.open_alerts > 0} to="/review" />
        <Fig k="Inbound credits held" v={d.held_inbound} warn={d.held_inbound > 0} to="/receiving" />
        <Fig k="P1 incidents" v={d.p1_incidents} bad={d.p1_incidents > 0} to="/incidents" />
        <Fig k={`Credits settling today (${credits.count})`} v={money(credits.amount_cents)} />
        <Fig k={`Debits settling today (${debits.count})`} v={money(debits.amount_cents)} />
      </div>

      <div className="split">
        <section className="panel flush">
          <h2>Latest files</h2>
          <div className="table-wrap">
            {files && files.length ? (
              <table>
                <thead><tr><th>Created</th><th>Direction</th><th>Window</th><th className="amt">Entries</th><th className="amt">Debits</th><th className="amt">Credits</th><th>Status</th><th>Reconciliation</th></tr></thead>
                <tbody>
                  {files.map((f) => (
                    <tr key={f.id}>
                      <td className="nowrap">{dateTimeET(f.created_at)}</td>
                      <td>{f.direction === "outbound" ? (f.kind === "returns" ? "Returns out" : "To FedACH") : "From FedACH"}</td>
                      <td>{f.window ?? "—"}</td>
                      <td className="amt">{f.entry_count}</td>
                      <td className="amt">{money(f.total_debit_cents)}</td>
                      <td className="amt">{money(f.total_credit_cents)}</td>
                      <td><Badge s={f.status} /></td>
                      <td>{f.reconciliation ? <Badge s={f.reconciliation} /> : <span className="muted">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty>No files yet today. Queued entries go out at the next cut-off.</Empty>}
          </div>
        </section>

        <section className="panel">
          <h2>Controls</h2>
          <dl className="kv">
            <dt>Ledger</dt><dd>{d.ledger_balanced ? <Badge s="matched" label="Balanced" /> : <Badge s="break" label="Out of balance" />}</dd>
            <dt>Reconciliation, 24 h</dt>
            <dd>{Object.keys(d.reconciliation_24h).length ? Object.entries(d.reconciliation_24h).map(([k, v]) => <span key={k} style={{ marginRight: 8 }}><Badge s={k} label={`${v} ${k}`} /></span>) : <span className="muted">No settlements yet</span>}</dd>
            <dt>Returns received today</dt><dd>{d.returns_today}</dd>
            <dt>Returns waiting to send</dt><dd>{d.pending_returns_out}</dd>
          </dl>
          <h3>Queued for release</h3>
          {d.queued_by_window.length ? (
            <table>
              <tbody>
                {d.queued_by_window.map((q: any) => (
                  <tr key={q.window}><td>{q.window}</td><td className="amt">{q.count} entries</td><td className="amt">{money(q.amount_cents)}</td></tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted">Nothing queued.</p>}
        </section>
      </div>
    </>
  );
}

function Fig({ k, v, warn, bad, to }: { k: string; v: any; warn?: boolean; bad?: boolean; to?: string }) {
  const inner = (<><div className="v">{v}</div><div className="k">{k}</div></>);
  return <div className={`figure ${bad ? "bad" : warn ? "warn" : ""}`}>{to ? <Link to={to} style={{ color: "inherit" }}>{inner}</Link> : inner}</div>;
}
