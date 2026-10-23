import { useState } from "react";
import { api } from "../lib/api";
import { dateTimeET } from "../lib/format";
import { Badge, Empty, PageHead, Seg, useAction, useApi } from "../components/ui";

const CAT: Record<string, string> = {
  window_at_risk: "Window at risk", window_missed: "Window missed", reconciliation_break: "Reconciliation break",
  file_rejected: "File rejected", duplicate_file: "Duplicate file", alert_sla: "Alert SLA", webhook_failure: "Webhook failures",
  return_rate_breach: "Return-rate breach", unmatched_return: "Unmatched return", inbound_hold_sla: "Inbound hold SLA",
};

export default function Incidents() {
  const [status, setStatus] = useState("open,acknowledged");
  const { data, reload } = useApi<any[]>(`/v1/incidents?status=${status}`, [status]);
  const { run, busy } = useAction();
  const [notes, setNotes] = useState<Record<string, string>>({});
  return (
    <>
      <PageHead title="Incidents" sub="Raised automatically by monitoring and forwarded to the service desk. P1 means money is late or the books don't agree." />
      <div className="filters"><Seg value={status} options={[["open,acknowledged", "Active"], ["resolved", "Resolved"]]} onChange={setStatus} /></div>
      <section className="panel flush">
        <div className="table-wrap">
          {data?.length ? (
            <table>
              <thead><tr><th>Priority</th><th>Opened</th><th>Category</th><th>What happened</th><th>Status</th><th></th></tr></thead>
              <tbody>{data.map((i) => (
                <tr key={i.id}>
                  <td><Badge s={i.priority === "P1" ? "P1" : i.priority === "P2" ? "held" : "quiet"} label={i.priority} /></td>
                  <td className="nowrap">{dateTimeET(i.opened_at)}</td>
                  <td>{CAT[i.category] ?? i.category}</td>
                  <td>{i.title}{i.external_ref && <div className="muted small">Ticket {i.external_ref}</div>}{i.resolution && <div className="muted small">{i.resolution}</div>}</td>
                  <td><Badge s={i.status} /></td>
                  <td>
                    {i.status !== "resolved" && (
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        {i.status === "open" && <button className="btn sm" disabled={busy} onClick={() => run(() => api(`/v1/incidents/${i.id}`, { method: "PATCH", body: { action: "acknowledge" } }), "Acknowledged", reload)}>Acknowledge</button>}
                        <input placeholder="Resolution" value={notes[i.id] ?? ""} onChange={(e) => setNotes({ ...notes, [i.id]: e.target.value })} style={{ width: 160 }} />
                        <button className="btn sm" disabled={busy || !notes[i.id]} onClick={() => run(() => api(`/v1/incidents/${i.id}`, { method: "PATCH", body: { action: "resolve", note: notes[i.id] } }), "Resolved", reload)}>Resolve</button>
                      </div>
                    )}
                  </td>
                </tr>))}
              </tbody>
            </table>
          ) : <Empty>{status === "resolved" ? "No resolved incidents." : "No active incidents."}</Empty>}
        </div>
      </section>
    </>
  );
}
