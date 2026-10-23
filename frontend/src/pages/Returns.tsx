import { useState } from "react";
import { dateOnly, dateTimeET, money } from "../lib/format";
import { Badge, Empty, PageHead, Seg, useApi } from "../components/ui";

export default function Returns() {
  const [tab, setTab] = useState<"received" | "sent" | "nocs" | "codes">("received");
  const { data: rets } = useApi<any[]>("/v1/returns");
  const { data: nocs } = useApi<any[]>("/v1/nocs");
  const { data: codes } = useApi<any[]>("/v1/reference/return-codes");
  const list = (rets ?? []).filter((r) => r.direction === tab);
  return (
    <>
      <PageHead title="Returns and NOCs" sub="Returns we receive on entries we sent, returns we send back on entries we received, and Notifications of Change." />
      <div className="filters">
        <Seg value={tab} options={[["received", "Returns received"], ["sent", "Returns sent"], ["nocs", "Notifications of Change"], ["codes", "Return code reference"]]} onChange={setTab} />
      </div>
      <section className="panel flush">
        <div className="table-wrap">
          {(tab === "received" || tab === "sent") && (list.length ? (
            <table>
              <thead><tr><th>When</th><th>Code</th><th>Category</th><th>Original trace</th><th className="amt">Amount</th><th>Deadline</th><th>Timely</th><th>Status</th></tr></thead>
              <tbody>{list.map((r) => (
                <tr key={r.id}>
                  <td className="nowrap">{dateTimeET(r.at)}</td>
                  <td><span className="chip">{r.reason_code}</span> <span className="small">{codes?.find((c) => c.code === r.reason_code)?.description}</span></td>
                  <td>{r.category}</td>
                  <td className="mono">{r.original_trace}</td>
                  <td className="amt">{money(r.amount_cents)}</td>
                  <td className="nowrap">{dateOnly(r.deadline)}</td>
                  <td>{r.timely ? "Yes" : <Badge s="returned" label="Late" />}</td>
                  <td><Badge s={r.status} /></td>
                </tr>))}
              </tbody>
            </table>
          ) : <Empty>{tab === "received" ? "No returns received. In the sandbox, open a settled payment and send a return." : "No returns sent yet."}</Empty>)}

          {tab === "nocs" && ((nocs ?? []).length ? (
            <table>
              <thead><tr><th>When</th><th>Direction</th><th>Code</th><th>Corrected data</th><th>Original trace</th><th>Apply by</th><th>Status</th></tr></thead>
              <tbody>{nocs!.map((n) => (
                <tr key={n.id}>
                  <td className="nowrap">{dateTimeET(n.at)}</td>
                  <td>{n.direction === "received" ? "From RDFI" : "To ODFI"}</td>
                  <td><span className="chip">{n.change_code}</span></td>
                  <td className="mono">{n.corrected_data}</td>
                  <td className="mono">{n.original_trace}</td>
                  <td className="nowrap">{dateOnly(n.apply_by)}</td>
                  <td><Badge s={n.status} /></td>
                </tr>))}
              </tbody>
            </table>
          ) : <Empty>No Notifications of Change.</Empty>)}

          {tab === "codes" && (
            <table>
              <thead><tr><th>Code</th><th>Meaning</th><th>Category</th><th>Return window</th><th>WSUD needed</th></tr></thead>
              <tbody>{codes?.map((c) => (
                <tr key={c.code}><td><span className="chip">{c.code}</span></td><td>{c.description}</td><td>{c.category}</td>
                  <td>{c.window} {c.window_unit} days</td><td>{c.requires_wsud ? "Yes" : "No"}</td></tr>))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </>
  );
}
