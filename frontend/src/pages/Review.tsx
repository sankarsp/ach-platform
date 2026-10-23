import { useState } from "react";
import { api, getSession } from "../lib/api";
import { dateTimeET } from "../lib/format";
import { PaymentDrawer } from "../components/PaymentDrawer";
import { InboundDrawer } from "./Receiving";
import { Badge, Empty, PageHead, Seg, useAction, useApi } from "../components/ui";

const KIND: Record<string, string> = {
  sanctions: "Sanctions", fraud: "Fraud", aml: "AML typology", exposure_limit: "Exposure limit", prefunding: "Prefunding",
  credit_monitoring: "Inbound credit monitoring", return_rate: "Return rate", untimely_return: "Untimely return",
};

export default function Review() {
  const [view, setView] = useState<"open" | "cases" | "closed">("open");
  const { data: alerts, reload } = useApi<any[]>(`/v1/alerts?status=${view === "closed" ? "closed" : "open,escalated"}`, [view]);
  const { data: cases, reload: reloadCases } = useApi<any[]>("/v1/cases");
  const [payment, setPayment] = useState<string | null>(null);
  const [inbound, setInbound] = useState<string | null>(null);
  const { run, busy } = useAction();
  const me = getSession()?.actor ?? "analyst";
  const refresh = () => { reload(); reloadCases(); };

  return (
    <>
      <PageHead title="Review queue" sub="Held payments and inbound credits wait here for a decision. Clear holds before the next cut-off or they roll to a later window." />
      <div className="filters">
        <Seg value={view} options={[["open", "Open alerts"], ["cases", "Cases"], ["closed", "Closed alerts"]]} onChange={setView} />
      </div>

      {view !== "cases" && (
        <section className="panel flush">
          <div className="table-wrap">
            {alerts && alerts.length ? (
              <table>
                <thead><tr><th>Raised</th><th>Type</th><th>Severity</th><th>What happened</th><th>Assignee</th><th>Status</th><th></th></tr></thead>
                <tbody>
                  {alerts.map((a) => (
                    <tr key={a.id}>
                      <td className="nowrap">{dateTimeET(a.created_at)}</td>
                      <td>{KIND[a.kind] ?? a.kind}</td>
                      <td className={`sev-${a.severity}`}>{a.severity}</td>
                      <td>
                        {a.title}
                        {a.details?.reasons?.length ? <ul className="reasons">{a.details.reasons.slice(0, 3).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul> : null}
                      </td>
                      <td>{a.assignee ?? <span className="muted">Unassigned</span>}</td>
                      <td><Badge s={a.status} /></td>
                      <td>
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          {a.payment_id && <button className="btn sm" onClick={() => setPayment(a.payment_id)}>Open payment</button>}
                          {a.inbound_entry_id && <button className="btn sm" onClick={() => setInbound(a.inbound_entry_id)}>Open entry</button>}
                          {view === "open" && !a.assignee && <button className="btn sm" disabled={busy} onClick={() => run(() => api(`/v1/alerts/${a.id}`, { method: "PATCH", body: { assignee: me } }), "Assigned to you", refresh)}>Take it</button>}
                          {view === "open" && !a.case_id && <button className="btn sm" disabled={busy} onClick={() => run(() => api("/v1/cases", { body: { title: a.title, alert_ids: [a.id] } }), "Case opened", refresh)}>Open case</button>}
                          {view === "open" && !a.payment_id && !a.inbound_entry_id && <button className="btn sm" disabled={busy} onClick={() => run(() => api(`/v1/alerts/${a.id}`, { method: "PATCH", body: { status: "closed", disposition: "no_action", note: "Reviewed" } }), "Alert closed", refresh)}>Close</button>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty>{view === "open" ? "The queue is clear. New holds appear here as payments are screened." : "No closed alerts yet."}</Empty>}
          </div>
        </section>
      )}

      {view === "cases" && <Cases cases={cases ?? []} onChanged={refresh} />}

      {payment && <PaymentDrawer id={payment} onClose={() => setPayment(null)} onChanged={refresh} />}
      {inbound && <InboundDrawer id={inbound} onClose={() => setInbound(null)} onChanged={refresh} />}
    </>
  );
}

function Cases({ cases, onChanged }: { cases: any[]; onChanged: () => void }) {
  const { run, busy } = useAction();
  const [sar, setSar] = useState<Record<string, string>>({});
  if (!cases.length) return <section className="panel"><Empty>No cases. Open one from an alert to investigate several related alerts together.</Empty></section>;
  return (
    <>
      {cases.map((c) => (
        <section className="panel" key={c.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>{c.title}</h2>
            <Badge s={c.status === "open" ? "open" : c.status === "sar_filed" ? "escalated" : "closed"} label={c.status.replace(/_/g, " ")} />
          </div>
          <p className="muted small">Opened {dateTimeET(c.created_at)}. {c.alerts.length} alert(s) linked.{c.sar_reference ? ` SAR reference ${c.sar_reference}.` : ""}</p>
          <ul className="reasons">{c.notes.map((n: any, i: number) => <li key={i}>{dateTimeET(n.at)}, {n.by}: {n.note}</li>)}</ul>
          {c.status === "open" && (
            <div className="row" style={{ marginTop: 10 }}>
              <input placeholder="BSA E-Filing reference" value={sar[c.id] ?? ""} onChange={(e) => setSar({ ...sar, [c.id]: e.target.value })} />
              <button className="btn" disabled={busy || !sar[c.id]} onClick={() => run(() => api(`/v1/cases/${c.id}`, { method: "PATCH", body: { action: "file_sar", sar_reference: sar[c.id], note: "SAR filed" } }), "SAR recorded", onChanged)}>Record SAR filing</button>
              <button className="btn" disabled={busy} onClick={() => run(() => api(`/v1/cases/${c.id}`, { method: "PATCH", body: { action: "close", note: "No suspicious activity found" } }), "Case closed", onChanged)}>Close with no action</button>
            </div>
          )}
        </section>
      ))}
    </>
  );
}
