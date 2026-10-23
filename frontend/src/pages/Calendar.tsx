import { Fragment, useState } from "react";
import { api } from "../lib/api";
import { dateOnly, timeET } from "../lib/format";
import { Badge, PageHead, Seg, useAction, useApi } from "../components/ui";

export default function CalendarPage() {
  const [tab, setTab] = useState<"days" | "windows" | "rules" | "sec">("days");
  const { data: cal, reload } = useApi<any>("/v1/reference/calendar");
  const { data: win } = useApi<any>("/v1/reference/windows");
  const { data: rules } = useApi<any>("/v1/reference/rules");
  const { data: sec } = useApi<any[]>("/v1/reference/sec-codes");
  const { data: uses } = useApi<any[]>("/v1/reference/use-cases");
  return (
    <>
      <PageHead title="Calendar and rules" sub="Settlement follows the Federal Reserve holiday schedule, not the federal-employee calendar: when a holiday falls on a Saturday the Fed is open the Friday before." />
      <div className="filters">
        <Seg value={tab} options={[["days", "Non-settlement days"], ["windows", "Windows and cut-offs"], ["rules", "Rules in force"], ["sec", "SEC codes and use cases"]]} onChange={setTab} />
      </div>
      {tab === "days" && cal && (
        <div className="split">
          <section className="panel flush">
            <table>
              <thead><tr><th>Date</th><th>Holiday</th><th>ACH settles?</th><th>Note</th></tr></thead>
              <tbody>{cal.non_settlement_days.map((d: any) => (
                <tr key={d.date}><td className="nowrap">{dateOnly(d.date)}</td><td>{d.holiday}</td>
                  <td>{d.settles ? <Badge s="settled" label="Yes, Fed open" /> : <Badge s="rejected" label="No" />}</td>
                  <td className="small muted">{d.note}</td></tr>))}
              </tbody>
            </table>
          </section>
          <div>
            <DateCheck />
            <Overrides cal={cal} onDone={reload} />
          </div>
        </div>
      )}
      {tab === "windows" && win && (
        <section className="panel flush">
          <table>
            <thead><tr><th>Window</th><th>Our cut-off (ET)</th><th>Fed deadline (ET)</th><th>Delivered to RDFIs</th><th>Settles</th></tr></thead>
            <tbody>{win.schedule.map((w: any) => (
              <tr key={w.window}><td><strong>{w.window}</strong> <span className="muted">{w.label}</span></td><td>{w.internal_cutoff_et}</td><td>{w.fed_deadline_et}</td><td>{w.distribution_et}</td><td>{w.settlement_et}, {w.settles}</td></tr>))}
            </tbody>
          </table>
        </section>
      )}
      {tab === "rules" && rules && (
        <div className="grid cols-2">
          <section className="panel">
            <h2>In force on {dateOnly(rules.as_of)}</h2>
            <dl className="kv">{rules.dated.map((r: any) => (
              <Fragment key={r.key}><dt>{r.description}</dt><dd>{fmt(r.value)}</dd></Fragment>))}
            </dl>
          </section>
          <section className="panel flush">
            <h2>Nacha rule calendar</h2>
            <table><tbody>{rules.upcoming.slice().reverse().map((u: any) => (
              <tr key={u.date}><td className="nowrap">{dateOnly(u.date)}</td><td>{u.rule}</td><td>{u.in_force ? <Badge s="settled" label="In force" /> : <Badge s="held" label="Upcoming" />}</td></tr>))}
            </tbody></table>
          </section>
        </div>
      )}
      {tab === "sec" && (
        <div className="grid">
          <section className="panel flush">
            <h2>Use cases</h2>
            <table><thead><tr><th>Use case</th><th>Direction</th><th>SEC code</th><th>Key rule</th></tr></thead>
              <tbody>{uses?.map((u) => <tr key={u.purpose}><td>{u.use_case}</td><td>{u.direction}</td><td><strong>{u.sec}</strong></td><td className="small">{u.rule}</td></tr>)}</tbody></table>
          </section>
          <section className="panel flush">
            <h2>SEC codes</h2>
            <table><thead><tr><th>Code</th><th>Receiver</th><th>Allows</th><th className="amt">Max addenda</th><th>Same Day</th><th>Authorization</th></tr></thead>
              <tbody>{sec?.map((s) => <tr key={s.code}><td><strong>{s.code}</strong> <span className="muted small">{s.name}</span></td><td>{s.receiver}</td><td>{[s.credits && "credits", s.debits && "debits"].filter(Boolean).join(", ")}</td><td className="amt">{s.max_addenda}</td><td>{s.same_day_eligible ? "Yes" : "No"}</td><td className="small">{s.authorization}</td></tr>)}</tbody></table>
          </section>
        </div>
      )}
    </>
  );
}

const fmt = (v: any) => (typeof v === "number" ? (v > 1000 ? `$${(v / 100).toLocaleString()}` : String(v))
  : typeof v === "object" && v ? Object.entries(v).map(([k, x]) => `${k}: ${typeof x === "number" && x < 1 ? `${(x * 100).toFixed(1)}%` : x}`).join(", ") || "None" : String(v));

function DateCheck() {
  const [d, setD] = useState("2027-07-05");
  const [sec, setSec] = useState("PPD");
  const [res, setRes] = useState<any>(null);
  return (
    <section className="panel">
      <h2>When would it settle?</h2>
      <div className="row" style={{ marginBottom: 10 }}>
        <input type="date" value={d} onChange={(e) => setD(e.target.value)} aria-label="Effective date" />
        <select value={sec} onChange={(e) => setSec(e.target.value)} aria-label="SEC code">{["PPD", "CCD", "WEB", "IAT"].map((s) => <option key={s}>{s}</option>)}</select>
        <button className="btn" onClick={async () => setRes(await api("/v1/reference/settlement-preview", { body: { effective_date: d, sec_code: sec } }))}>Check</button>
      </div>
      {res && (
        <>
          <p>Settles <strong>{dateOnly(res.settlement_date)}</strong> at {timeET(res.settlement)} ET in {res.label}.</p>
          {res.reasons.map((r: string, i: number) => <div key={i} className="note info">{r}</div>)}
        </>
      )}
    </section>
  );
}

function Overrides({ cal, onDone }: { cal: any; onDone: () => void }) {
  const [day, setDay] = useState("");
  const [kind, setKind] = useState("closure");
  const [note, setNote] = useState("");
  const { run, busy } = useAction();
  return (
    <section className="panel">
      <h2>Unscheduled closures</h2>
      <p className="small muted">For a Fed announcement outside the standard schedule. One person requests, a different person approves.</p>
      <div className="grid cols-2">
        <div className="field"><label>Date</label><input type="date" value={day} onChange={(e) => setDay(e.target.value)} /></div>
        <div className="field"><label>Change</label><select value={kind} onChange={(e) => setKind(e.target.value)}><option value="closure">Fed closed</option><option value="opening">Fed open</option></select></div>
      </div>
      <div className="field"><label>Reason</label><input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Fed announcement reference" /></div>
      <div className="row">
        <button className="btn" disabled={busy || !day || !note} onClick={() => run(() => api("/v1/calendar/overrides", { body: { day, kind, note } }), "Change requested; a second person must approve it", onDone)}>Request change</button>
        <button className="btn primary" disabled={busy || !day} onClick={() => run(() => api(`/v1/calendar/overrides/${day}/approve`, { body: {} }), "Change approved and in effect", onDone)}>Approve change for this date</button>
      </div>
      {(cal.overrides.closures.length > 0 || cal.overrides.openings.length > 0) && (
        <p className="small" style={{ marginTop: 10 }}>In effect: {[...cal.overrides.closures.map((d: string) => `closed ${d}`), ...cal.overrides.openings.map((d: string) => `open ${d}`)].join(", ")}</p>
      )}
    </section>
  );
}
