import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, newKey } from "../lib/api";
import { addDays, dateOnly, money, timeET, todayET } from "../lib/format";
import { PageHead, useAction, useApi, useToast } from "../components/ui";

const PURPOSE_DEFAULTS: Record<string, { direction: string; sec: string[]; desc?: string }> = {
  payroll: { direction: "credit", sec: ["PPD", "CCD"], desc: "PAYROLL" },
  pension_benefits: { direction: "credit", sec: ["PPD"], desc: "PENSION" },
  bill_payment: { direction: "debit", sec: ["PPD", "WEB", "TEL", "CCD"], desc: "BILL PAY" },
  loan_disbursement: { direction: "credit", sec: ["PPD", "CCD"], desc: "LOAN" },
  loan_repayment: { direction: "debit", sec: ["PPD", "WEB", "CCD"], desc: "LOAN PMT" },
  purchase: { direction: "debit", sec: ["WEB"], desc: "PURCHASE" },
  p2p: { direction: "credit", sec: ["WEB"], desc: "TRANSFER" },
  vendor: { direction: "credit", sec: ["CCD", "CTX"], desc: "VENDOR PAY" },
  tax: { direction: "debit", sec: ["CCD"], desc: "TAX PMT" },
  child_support: { direction: "credit", sec: ["CCD"], desc: "CHILD SUPP" },
  cash_concentration: { direction: "debit", sec: ["CCD"], desc: "SWEEP" },
  refund: { direction: "credit", sec: ["PPD", "CCD", "WEB"], desc: "REFUND" },
};

export default function NewPayment() {
  const nav = useNavigate();
  const toast = useToast();
  const { data: orgs } = useApi<any[]>("/v1/originators");
  const { data: cases } = useApi<any[]>("/v1/reference/use-cases");
  const [orgId, setOrgId] = useState("");
  const { data: cps } = useApi<any[]>(orgId ? `/v1/counterparties?originator_id=${orgId}` : null, [orgId]);
  const org = orgs?.find((o) => o.id === orgId);

  const [purpose, setPurpose] = useState("payroll");
  const [cpId, setCpId] = useState("");
  const [direction, setDirection] = useState("credit");
  const [sec, setSec] = useState("PPD");
  const [amount, setAmount] = useState("");
  const [eff, setEff] = useState(todayET());
  const [sameDay, setSameDay] = useState(false);
  const [desc, setDesc] = useState("PAYROLL");
  const [addenda, setAddenda] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const { run, busy } = useAction();

  useEffect(() => { if (orgs?.length && !orgId) setOrgId(orgs[0].id); }, [orgs, orgId]);
  const cp = cps?.find((c) => c.id === cpId);

  // Suggest SEC/direction/description from the use case, constrained by what the originator and receiver allow
  const allowedSec = useMemo(() => {
    const base = PURPOSE_DEFAULTS[purpose]?.sec ?? ["PPD", "CCD", "CTX", "WEB", "TEL"];
    const byOrg = base.filter((s) => org?.allowed_sec_codes?.includes(s));
    const byParty = cp ? byOrg.filter((s) => (cp.party_type === "business" ? ["CCD", "CTX"] : ["PPD", "WEB", "TEL"]).includes(s)) : byOrg;
    return byParty;
  }, [purpose, org, cp]);
  useEffect(() => {
    const d = PURPOSE_DEFAULTS[purpose];
    if (d) { setDirection(d.direction); if (d.desc) setDesc(d.desc); }
  }, [purpose]);
  useEffect(() => { if (allowedSec.length && !allowedSec.includes(sec)) setSec(allowedSec[0]); }, [allowedSec, sec]);

  const cents = Math.round(parseFloat(amount || "0") * 100);
  useEffect(() => {
    const t = setTimeout(() => {
      api("/v1/reference/settlement-preview", { body: { effective_date: eff, sec_code: sec, amount_cents: cents || 100, same_day: sameDay } })
        .then(setPreview).catch(() => setPreview(null));
    }, 250);
    return () => clearTimeout(t);
  }, [eff, sec, cents, sameDay]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const ok = await run(async () => {
      const p: any = await api("/v1/payments", {
        headers: { "Idempotency-Key": newKey() },
        body: {
          originator_id: orgId, counterparty_id: cpId, amount_cents: cents, direction, sec_code: sec, effective_date: eff,
          purpose, company_entry_description: desc || null, same_day: sameDay,
          addenda: addenda.split("\n").map((s) => s.trim()).filter(Boolean),
        },
      });
      if (p.status === "held") toast(`Created and put on hold: ${p.status_reason}`, true);
    }, "Payment created");
    if (ok) nav("/payments");
  }

  return (
    <>
      <PageHead title="New payment" sub="Pick the use case first. The form limits the SEC code to what the originator is approved for and what the receiver type allows." />
      <div className="split">
        <form className="panel" onSubmit={submit}>
          <div className="grid cols-2">
            <div className="field">
              <label htmlFor="org">Originator</label>
              <select id="org" value={orgId} onChange={(e) => { setOrgId(e.target.value); setCpId(""); }}>
                {orgs?.map((o) => <option key={o.id} value={o.id}>{o.name} ({o.company_id})</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="purpose">Use case</label>
              <select id="purpose" value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                {cases?.filter((c) => c.purpose !== "cross_border").map((c) => <option key={c.purpose} value={c.purpose}>{c.use_case}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label htmlFor="cp">Receiver</label>
            <select id="cp" value={cpId} onChange={(e) => setCpId(e.target.value)} required>
              <option value="">Choose a receiver…</option>
              {cps?.map((c) => <option key={c.id} value={c.id} disabled={c.status !== "active"}>{c.name} · {c.party_type} · ending {c.account_last4}{c.status !== "active" ? " (blocked)" : ""}</option>)}
            </select>
            {cp && <span className="hint">Account validation: {cp.validation_status}{cp.account_changed_at ? ". Bank details changed recently, so credits will be held for call-back." : ""}</span>}
          </div>
          <div className="grid cols-2">
            <div className="field">
              <label htmlFor="sec">SEC code</label>
              <select id="sec" value={sec} onChange={(e) => setSec(e.target.value)}>
                {allowedSec.map((s) => <option key={s}>{s}</option>)}
              </select>
              {!allowedSec.length && <span className="hint">This originator isn't approved for any SEC code that fits this use case and receiver.</span>}
            </div>
            <div className="field">
              <label htmlFor="dir">Direction</label>
              <select id="dir" value={direction} onChange={(e) => setDirection(e.target.value)}>
                <option value="credit">Credit (send money)</option>
                <option value="debit">Debit (collect money)</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="amt">Amount (USD)</label>
              <input id="amt" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" required pattern="^\d+(\.\d{1,2})?$" />
            </div>
            <div className="field">
              <label htmlFor="eff">Effective date</label>
              <input id="eff" type="date" value={eff} min={todayET()} max={addDays(todayET(), 60)} onChange={(e) => setEff(e.target.value)} required />
            </div>
          </div>
          <div className="field">
            <label htmlFor="desc">Company entry description</label>
            <input id="desc" value={desc} maxLength={10} onChange={(e) => setDesc(e.target.value.toUpperCase())} />
            <span className="hint">Up to 10 characters. Wage credits must say PAYROLL and online purchases PURCHASE.</span>
          </div>
          <div className="field">
            <label htmlFor="add">Addenda</label>
            <textarea id="add" rows={2} value={addenda} onChange={(e) => setAddenda(e.target.value)} placeholder={sec === "CTX" ? "One EDI 820 segment per line" : "Optional remittance text, one line"} />
          </div>
          <label className="check" style={{ marginBottom: 16 }}>
            <input type="checkbox" checked={sameDay} onChange={(e) => setSameDay(e.target.checked)} disabled={!org?.same_day_enabled} />
            Settle same day {!org?.same_day_enabled && <span className="muted">(not enabled for this originator)</span>}
          </label>
          <button className="btn primary" disabled={busy || !cpId || !cents || !allowedSec.length}>Create payment</button>
        </form>

        <aside className="panel">
          <h2>When it settles</h2>
          {preview ? (
            <>
              <dl className="kv">
                <dt>Window</dt><dd>{preview.label} ({preview.window})</dd>
                <dt>Released at</dt><dd>{timeET(preview.internal_cutoff)} ET, {dateOnly(preview.internal_cutoff.slice(0, 10))}</dd>
                <dt>Fed deadline</dt><dd>{timeET(preview.fed_deadline)} ET</dd>
                <dt>Settles</dt><dd><strong>{dateOnly(preview.settlement_date)}</strong> at {timeET(preview.settlement)} ET</dd>
                <dt>Same Day</dt><dd>{preview.same_day_settlement ? "Yes" : "No"}</dd>
              </dl>
              {preview.reasons.length > 0 && <div className="note info">{preview.reasons.map((r: string, i: number) => <div key={i}>{r}</div>)}</div>}
              {cents > 0 && <p className="muted small">{money(cents)} {direction === "credit" ? "leaves the originator's account at release." : "is collected at settlement."}</p>}
            </>
          ) : <p className="muted">Enter a date to see the settlement plan.</p>}
          <p className="muted small">Weekends and Federal Reserve holidays move the settlement date to the next banking day. When a holiday falls on a Saturday, the Fed is open the Friday before.</p>
        </aside>
      </div>
    </>
  );
}
