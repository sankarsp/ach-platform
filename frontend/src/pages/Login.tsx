import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setSession } from "../lib/api";

export default function Login() {
  const nav = useNavigate();
  const [key, setKey] = useState(import.meta.env.DEV ? "sk_ops_demo" : "");
  const [actor, setActor] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSession({ key, actor: actor.trim() || "analyst" });
    try {
      await api("/v1/ops/dashboard");
      nav("/");
    } catch (e: any) {
      setSession(null);
      setErr(e.status === 403 ? "This key isn't an operator key. Use an operations or admin key." : e.message);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>ACH Operations</h1>
        <p>Sign in with an operator API key. Your name is recorded on every decision you make.</p>
        <div className="field">
          <label htmlFor="actor">Your name</label>
          <input id="actor" value={actor} onChange={(e) => setActor(e.target.value)} placeholder="e.g. Priya Raman" required autoFocus />
        </div>
        <div className="field">
          <label htmlFor="key">Operator API key</label>
          <input id="key" type="password" value={key} onChange={(e) => setKey(e.target.value)} required />
          <span className="hint">Sandbox key: sk_ops_demo</span>
        </div>
        {err && <div className="note bad">{err}</div>}
        <button className="btn primary" type="submit" style={{ width: "100%" }}>Sign in</button>
      </form>
    </div>
  );
}
