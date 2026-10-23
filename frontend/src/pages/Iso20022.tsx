import { useState } from "react";
import { apiXml, downloadText } from "../lib/api";
import { dateTimeET, money, todayET } from "../lib/format";
import { PaymentDrawer } from "../components/PaymentDrawer";
import { Badge, Drawer, Empty, PageHead, Seg, useAction, useApi, useToast } from "../components/ui";

const STATUS: Record<string, [string, string]> = {
  ACTC: ["Validated", "transmitted"], ACCP: ["Accepted", "transmitted"], ACSP: ["Sent to Fed", "transmitted"], ACSC: ["Settled", "settled"],
  PDNG: ["On hold", "held"], RJCT: ["Rejected", "rejected"], PART: ["Partly accepted", "held"], CANC: ["Cancelled", "rejected"],
  BOOK: ["Booked", "settled"],
};
const IsoBadge = ({ s }: { s: string }) => <Badge s={STATUS[s]?.[1] ?? "quiet"} label={`${STATUS[s]?.[0] ?? s} (${s})`} />;

export default function Iso20022() {
  const [tab, setTab] = useState<"received" | "submit" | "reports">("received");
  const { data: msgs, reload } = useApi<any[]>("/v1/iso20022/messages?limit=200");
  const [open, setOpen] = useState<string | null>(null);
  const inbound = (msgs ?? []).filter((m) => m.direction === "inbound");
  const outbound = (msgs ?? []).filter((m) => m.direction === "outbound");

  return (
    <>
      <PageHead title="ISO 20022"
        sub="Corporate clients send pain.001 credit transfers and pain.008 direct debits. Each transaction becomes an ACH entry through the same rules and screening as the API. Status goes back as pain.002; cash reporting as camt.054 and camt.053." />
      <div className="filters">
        <Seg value={tab} options={[["received", "Received messages"], ["submit", "Submit a message"], ["reports", "Cash reports"]]} onChange={setTab} />
      </div>

      {tab === "received" && (
        <>
          <section className="panel flush">
            <div className="table-wrap">
              {inbound.length ? (
                <table>
                  <thead><tr><th>Received</th><th>Type</th><th>Message ID</th><th className="amt">Transactions</th><th className="amt">Control sum</th><th>Status at intake</th><th>Outcome</th></tr></thead>
                  <tbody>{inbound.map((m) => (
                    <tr key={m.id} className="click" onClick={() => setOpen(m.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(m.id)}>
                      <td className="nowrap">{dateTimeET(m.created_at)}</td>
                      <td><span className="chip">{m.msg_type}</span></td>
                      <td className="mono">{m.msg_id}</td>
                      <td className="amt">{m.nb_of_txs}</td>
                      <td className="amt">{money(m.ctrl_sum_cents)}</td>
                      <td><IsoBadge s={m.status} /></td>
                      <td className="small">{Object.entries(m.tx_status_counts).map(([k, v]) => `${v} ${STATUS[k]?.[0].toLowerCase() ?? k}`).join(", ")}</td>
                    </tr>))}
                  </tbody>
                </table>
              ) : <Empty>No ISO 20022 messages yet. Submit one of the samples to see the flow.</Empty>}
            </div>
          </section>
          {outbound.length > 0 && (
            <section className="panel flush" style={{ marginTop: 16 }}>
              <h2>Messages we produced</h2>
              <table>
                <thead><tr><th>Created</th><th>Type</th><th>Message ID</th><th>Status</th><th></th></tr></thead>
                <tbody>{outbound.slice(0, 20).map((m) => (
                  <tr key={m.id}><td className="nowrap">{dateTimeET(m.created_at)}</td><td><span className="chip">{m.msg_type}</span></td>
                    <td className="mono">{m.msg_id}</td><td><IsoBadge s={m.status} /></td>
                    <td><DownloadBtn label="Download" path={`/v1/iso20022/messages/${m.id}/xml`} name={`${m.msg_type}-${m.msg_id}.xml`} /></td></tr>))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}

      {tab === "submit" && <Submit onDone={(id) => { reload(); setTab("received"); setOpen(id); }} />}
      {tab === "reports" && <Reports onDone={reload} />}
      {open && <MessageDrawer id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}

function DownloadBtn({ label, path, name }: { label: string; path: string; name: string }) {
  const toast = useToast();
  return (
    <button className="btn sm" onClick={async (e) => {
      e.stopPropagation();
      try { downloadText(name, await apiXml(path)); } catch (err: any) { toast(err.message, true); }
    }}>{label}</button>
  );
}

function Submit({ onDone }: { onDone: (id: string) => void }) {
  const { data: samples } = useApi<any[]>("/v1/iso20022/samples");
  const { data: orgs } = useApi<any[]>("/v1/originators");
  const [xml, setXml] = useState("");
  const [orgId, setOrgId] = useState("");
  const { run, busy } = useAction();
  const kind = xml.includes("CstmrDrctDbtInitn") ? "pain.008" : xml.includes("CstmrCdtTrfInitn") ? "pain.001" : null;
  return (
    <div className="split">
      <section className="panel">
        <div className="field">
          <label htmlFor="xml">Message</label>
          <textarea id="xml" rows={20} value={xml} onChange={(e) => setXml(e.target.value)} placeholder="Paste a pain.001 or pain.008 document, or load a sample" />
          <input type="file" accept=".xml" aria-label="Upload XML file" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setXml(await f.text()); }} />
        </div>
        <div className="field">
          <label htmlFor="org">Originator</label>
          <select id="org" value={orgId} onChange={(e) => setOrgId(e.target.value)}>
            <option value="">Work it out from the message (initiating party or account)</option>
            {orgs?.map((o) => <option key={o.id} value={o.id}>{o.name} ({o.company_id})</option>)}
          </select>
        </div>
        <button className="btn primary" disabled={busy || !kind} onClick={() => run(async () => {
          const r = JSON.parse(await apiXml(`/v1/iso20022/${kind}${orgId ? `?originator_id=${orgId}` : ""}`, xml));
          onDone(r.message.id);
        }, `${kind} received; status report produced`)}>
          {kind ? `Submit ${kind}` : "Submit message"}
        </button>
      </section>
      <aside className="panel">
        <h2>Samples</h2>
        <p className="small muted">Dated from today for the sandbox originators. Each one includes a transaction that should be rejected so you can see it in the status report.</p>
        {samples?.map((s) => (
          <div key={s.name} style={{ marginBottom: 12 }}>
            <button className="btn sm" onClick={async () => setXml(await apiXml(`/v1/iso20022/samples/${s.name}`))}>Load {s.name}</button>
            <div className="small" style={{ marginTop: 4 }}>{s.description}</div>
          </div>
        ))}
        <h3 style={{ marginTop: 16 }}>How fields map to ACH</h3>
        <ul className="reasons">
          <li>Category purpose SALA becomes PPD with description PAYROLL; SUPP becomes CCD, or CTX when remittance is structured.</li>
          <li>A creditor outside the US becomes an IAT entry.</li>
          <li>Service level SDVA requests Same Day ACH.</li>
          <li>Local instrument PPD, CCD, CTX, WEB or IAT overrides the choice.</li>
          <li>Direct debits need a mandate ID and signature date.</li>
        </ul>
      </aside>
    </div>
  );
}

function MessageDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { data: m } = useApi<any>(`/v1/iso20022/messages/${id}`);
  const [pay, setPay] = useState<string | null>(null);
  if (!m) return <Drawer title="Message" onClose={onClose}><p className="muted">Loading…</p></Drawer>;
  return (
    <Drawer title={`${m.msg_type} ${m.msg_id}`} sub={<IsoBadge s={m.status} />} onClose={onClose}>
      {m.status_reason && <div className="note bad">{m.status_reason}</div>}
      <div className="row" style={{ marginBottom: 16 }}>
        <DownloadBtn label="Download original" path={`/v1/iso20022/messages/${id}/xml`} name={`${m.msg_type}-${m.msg_id}.xml`} />
        <DownloadBtn label="Download current pain.002" path={`/v1/iso20022/messages/${id}/pain.002`} name={`pain.002-${m.msg_id}.xml`} />
      </div>
      <table>
        <thead><tr><th>End-to-end ID</th><th className="amt">Amount</th><th>Status now</th><th>Detail</th></tr></thead>
        <tbody>{m.results.map((r: any, i: number) => (
          <tr key={i} className={r.payment_id ? "click" : ""} onClick={() => r.payment_id && setPay(r.payment_id)}>
            <td className="mono">{r.end_to_end_id}<div className="muted small">{r.pmt_inf_id}</div></td>
            <td className="amt">{money(r.amount_cents)}</td>
            <td><IsoBadge s={r.current_status} />{r.current_reason && <div className="small"><span className="chip">{r.current_reason}</span></div>}</td>
            <td className="small">{r.current_info}</td>
          </tr>))}
        </tbody>
      </table>
      <p className="muted small" style={{ marginTop: 10 }}>Select a row with a payment to open it. The pain.002 always reflects the latest state, so download it again after settlement or a return.</p>
      {pay && <PaymentDrawer id={pay} onClose={() => setPay(null)} onChanged={onChanged} />}
    </Drawer>
  );
}

function Reports({ onDone }: { onDone: () => void }) {
  const { data: orgs } = useApi<any[]>("/v1/originators");
  const [orgId, setOrgId] = useState("");
  const [day, setDay] = useState(todayET());
  const toast = useToast();
  const get = async (kind: string) => {
    const o = orgId || orgs?.[0]?.id;
    try {
      downloadText(`${kind}-${day}.xml`, await apiXml(`/v1/iso20022/${kind}?originator_id=${o}&day=${day}`));
      onDone();
    } catch (e: any) { toast(e.message, true); }
  };
  return (
    <section className="panel" style={{ maxWidth: 640 }}>
      <h2>Cash reports for an originator</h2>
      <div className="grid cols-2">
        <div className="field"><label htmlFor="ro">Originator</label>
          <select id="ro" value={orgId} onChange={(e) => setOrgId(e.target.value)}>
            {orgs?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select></div>
        <div className="field"><label htmlFor="rd">Booking day</label><input id="rd" type="date" value={day} onChange={(e) => setDay(e.target.value)} /></div>
      </div>
      <div className="row">
        <button className="btn" onClick={() => get("camt.054")}>Download camt.054 notification</button>
        <button className="btn" onClick={() => get("camt.053")}>Download camt.053 statement</button>
      </div>
      <p className="small muted" style={{ marginTop: 12 }}>camt.054 lists the day's settled entries and returns, with ISO and Nacha reason codes; Notifications of Change are in the additional information. camt.053 is the end-of-day statement of the originator's settlement account, with opening and closing balances.</p>
    </section>
  );
}
