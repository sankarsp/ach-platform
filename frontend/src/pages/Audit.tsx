import { dateTimeET } from "../lib/format";
import { Empty, PageHead, useApi } from "../components/ui";

export default function Audit() {
  const { data } = useApi<any[]>("/v1/audit?limit=500");
  return (
    <>
      <PageHead title="Audit log" sub="Who did what and when. Append-only; kept as evidence for the annual Nacha Rules compliance audit." />
      <section className="panel flush">
        <div className="table-wrap">
          {data?.length ? (
            <table>
              <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Detail</th></tr></thead>
              <tbody>{data.map((a, i) => (
                <tr key={i}>
                  <td className="nowrap">{dateTimeET(a.at)}</td><td>{a.actor}</td><td>{a.action}</td>
                  <td className="small">{a.entity} <span className="mono">{a.entity_id.slice(0, 12)}</span></td>
                  <td className="small muted">{Object.entries(a.detail).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("; ")}</td>
                </tr>))}
              </tbody>
            </table>
          ) : <Empty>No activity recorded yet.</Empty>}
        </div>
      </section>
    </>
  );
}
