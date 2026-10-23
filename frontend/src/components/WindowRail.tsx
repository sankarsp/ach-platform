import { useEffect, useState } from "react";
import { timeET } from "../lib/format";

type W = {
  window: string; label: string; same_day: boolean; internal_cutoff: string; fed_deadline: string;
  settlement: string; settlement_date: string; minutes_to_cutoff: number; state: "open" | "at_risk" | "closed";
};
type Queue = { window: string; count: number; amount_cents: number };

/** Today's FedACH windows on one time rail (06:00–24:00 ET) with a live now-marker. */
export function WindowRail({ windows, queued, banking, holiday }: { windows: W[]; queued: Queue[]; banking: boolean; holiday?: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const etHours = (iso: string | number) => {
    const d = new Date(iso);
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23" }).formatToParts(d);
    const h = Number(parts.find((p) => p.type === "hour")?.value);
    const m = Number(parts.find((p) => p.type === "minute")?.value);
    return h + m / 60;
  };
  const START = 0, END = 24;
  const x = (h: number) => `${((Math.min(Math.max(h, START), END) - START) / (END - START)) * 100}%`;
  const nowH = etHours(now);
  const next = windows.find((w) => w.state !== "closed");
  const minsLive = (w: W) => Math.round((new Date(w.internal_cutoff).getTime() - now) / 60000);
  const stateLive = (w: W) => {
    const m = minsLive(w);
    return m < 0 ? "closed" : m <= 30 ? "at_risk" : "open";
  };
  const q = (code: string) => queued.find((x) => x.window === code);

  let headline = "No more windows today";
  if (!banking) headline = holiday ? `Fed closed: ${holiday}` : "Fed closed (weekend): entries queue for the next banking day";
  else if (next) {
    const m = minsLive(next);
    const hh = Math.floor(m / 60), mm = m % 60;
    headline = `${next.label} cut-off in ${hh ? `${hh} h ` : ""}${mm} min`;
  }

  return (
    <section className="rail" aria-label="Today's FedACH windows">
      <div className="rail-head">
        <h2 style={{ margin: 0 }}>{headline}</h2>
        <span className="day">Our cut-off is 45 minutes before each Fed deadline. Times are Eastern.</span>
      </div>
      <div className="rail-track">
        <div className="rail-line" />
        <div className="rail-past" style={{ width: x(nowH) }} />
        {[0, 6, 12, 18, 24].map((h) => (
          <div key={h} className={`rail-hour ${h === 24 ? "end" : h === 0 ? "start" : ""}`} style={{ left: x(h) }}>
            {h === 0 || h === 24 ? "12 a.m." : h === 12 ? "noon" : h < 12 ? `${h} a.m.` : `${h - 12} p.m.`}
          </div>
        ))}
        {windows.map((w) => {
          const st = stateLive(w);
          const qq = q(w.window);
          return (
            <div key={w.window + w.internal_cutoff} className={`rail-mark ${st} ${next?.window === w.window ? "next" : ""}`}
                 style={{ left: x(etHours(w.internal_cutoff)) }} title={`Fed deadline ${timeET(w.fed_deadline)}; settles ${timeET(w.settlement)} on ${w.settlement_date}`}>
              <div className="dot" />
              <div className="lbl">
                <b>{w.window}</b>
                <span>{timeET(w.internal_cutoff)}</span>
                {qq && st !== "closed" ? <span style={{ display: "block", color: "var(--ink)" }}>{qq.count} queued</span> : null}
              </div>
            </div>
          );
        })}
        {banking && (
          <div className="rail-now" style={{ left: x(nowH) }}>
            <span>now {timeET(new Date(now).toISOString())}</span>
          </div>
        )}
      </div>
    </section>
  );
}
