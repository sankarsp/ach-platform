const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const ET = "America/New_York";

export const money = (cents?: number | null) => (cents == null ? "—" : usd.format(cents / 100));

export const dateOnly = (d?: string | null) => {
  if (!d) return "—";
  const [y, m, day] = d.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, day)).toLocaleDateString("en-US", {
    timeZone: "UTC", weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
};

export const timeET = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { timeZone: ET, hour: "numeric", minute: "2-digit" }) : "—";

export const dateTimeET = (iso?: string | null) =>
  iso
    ? new Date(iso).toLocaleString("en-US", { timeZone: ET, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "—";

export const pct = (x: number) => `${(x * 100).toFixed(2)}%`;

export const todayET = () => new Date().toLocaleDateString("en-CA", { timeZone: ET });

export const addDays = (iso: string, n: number) => {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

export const statusLabel: Record<string, string> = {
  received: "Received", held: "On hold", warehoused: "Queued", transmitted: "Sent to Fed", settled: "Settled",
  returned: "Returned", rejected: "Rejected", cancelled: "Cancelled", reversed: "Reversed",
  posted: "Posted", return_pending: "Return queued", open: "Open", acknowledged: "Acknowledged", resolved: "Resolved",
  escalated: "Escalated", closed: "Closed", matched: "Matched", break: "Break", applied: "Applied", sent: "Sent",
  pending: "Pending", processed: "Processed", built: "Built", rejected_file: "Rejected",
};

export const tone = (s: string): "good" | "warn" | "bad" | "info" | "quiet" => {
  if (["settled", "posted", "matched", "applied", "resolved", "sent", "processed", "acknowledged"].includes(s)) return "good";
  if (["held", "at_risk", "escalated", "return_pending", "pending", "open"].includes(s)) return "warn";
  if (["returned", "rejected", "break", "closed_window", "critical", "P1"].includes(s)) return "bad";
  if (["transmitted", "warehoused", "received"].includes(s)) return "info";
  return "quiet";
};
