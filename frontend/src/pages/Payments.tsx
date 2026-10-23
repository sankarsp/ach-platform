import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { dateOnly, money } from "../lib/format";
import { PaymentDrawer } from "../components/PaymentDrawer";
import { Badge, Empty, PageHead, Seg, useApi } from "../components/ui";

const FILTERS: [string, string][] = [
  ["", "All"], ["held", "On hold"], ["warehoused", "Queued"], ["transmitted", "Sent"], ["settled", "Settled"],
  ["returned", "Returned"], ["rejected,cancelled", "Rejected or cancelled"],
];

export default function Payments() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const [open, setOpen] = useState<string | null>(null);
  const { data, reload } = useApi<any[]>(`/v1/payments?limit=300${status ? `&status=${status}` : ""}`);
  const { data: orgs } = useApi<any[]>("/v1/originators");
  const orgName = (id: string) => orgs?.find((o) => o.id === id)?.name ?? "";

  return (
    <>
      <PageHead title="Payments" sub="Every entry we originate, from intake through settlement and returns."
        actions={<Link className="btn primary" to="/payments/new">New payment</Link>} />
      <div className="filters">
        <Seg value={status} options={FILTERS} onChange={(v) => setParams(v ? { status: v } : {})} />
      </div>
      <section className="panel flush">
        <div className="table-wrap">
          {data && data.length ? (
            <table>
              <thead>
                <tr><th>Originator</th><th>SEC</th><th>Direction</th><th className="amt">Amount</th><th>Description</th><th>Window</th><th>Settles</th><th>Status</th><th className="amt">Fraud score</th></tr>
              </thead>
              <tbody>
                {data.map((p) => (
                  <tr key={p.id} className="click" onClick={() => setOpen(p.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(p.id)}>
                    <td>{orgName(p.originator_id)}</td>
                    <td><span className="chip">{p.sec_code}</span></td>
                    <td>{p.direction}</td>
                    <td className="amt">{money(p.amount_cents)}</td>
                    <td>{p.company_entry_description}</td>
                    <td>{p.window_code}{p.same_day ? " (same day)" : ""}</td>
                    <td className="nowrap">{dateOnly(p.settlement_date)}</td>
                    <td><Badge s={p.status} />{p.return_code ? <span className="muted small"> {p.return_code}</span> : null}</td>
                    <td className="amt" style={{ color: p.risk_score >= 60 ? "var(--crimson)" : undefined }}>{p.risk_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No payments match this filter.</Empty>}
        </div>
      </section>
      {open && <PaymentDrawer id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}
