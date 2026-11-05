import { useState } from "react";
import { api, type AskResponse } from "../api";
import { Chip } from "../components";

const SUGGESTIONS = [
  "What happens if the MICU oxygen zone valve fails?",
  "How many free ICU beds are there?",
  "How many ventilators are available hospital-wide?",
  "What if we lose utility power and generator 2 fails?",
  "How many CCRN nurses work in the SICU?",
  "What does AST-VENTILATOR-0003 depend on?",
];

type Turn = { q: string; a?: AskResponse; err?: string };

export function renderAnswer(text: string, onCite: (id: string) => void) {
  const parts = text.split(/(\[[A-Z][A-Z0-9-]+\])/g);
  return parts.map((p, i) => {
    const m = p.match(/^\[([A-Z][A-Z0-9-]+)\]$/);
    return m ? <Chip key={i} id={m[1]} onClick={onCite} /> : <span key={i}>{p}</span>;
  });
}

export default function Assistant({ onInspect }: { onInspect: (id: string) => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const ask = async (question: string) => {
    if (!question.trim()) return;
    setBusy(true); setQ("");
    setTurns((t) => [...t, { q: question }]);
    try {
      const a = await api.ask(question);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, err: String(e) } : x)));
    }
    setBusy(false);
  };

  return (
    <>
      <h1>Operations assistant</h1>
      <p className="lede">Answers come only from graph tool calls; every bracketed id is a verified citation you can open.</p>
      <div className="suggest" style={{ marginBottom: 12 }}>
        {SUGGESTIONS.map((s) => <button key={s} className="btn ghost small" onClick={() => ask(s)} disabled={busy}>{s}</button>)}
      </div>
      <div className="chat">
        {turns.map((t, i) => (
          <div key={i} className="chat">
            <div className="msg user">{t.q}</div>
            {t.err && <div className="msg bot err">{t.err}</div>}
            {t.a && (
              <div className="msg bot">
                <div>{renderAnswer(t.a.answer, onInspect)}</div>
                <div className="small muted" style={{ marginTop: 6 }}>
                  {t.a.grounded ? "✓ citations verified" : "⚠ not fully grounded"} · {t.a.provider} ·{" "}
                  {t.a.tool_calls.map((c) => c.tool).join(", ") || "no tools"} · {t.a.latency_ms.toFixed(0)} ms
                </div>
              </div>
            )}
            {!t.a && !t.err && <div className="msg bot muted">Thinking…</div>}
          </div>
        ))}
      </div>
      <form className="row" style={{ marginTop: 16 }} onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input style={{ flex: 1, minWidth: 240 }} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Ask about beds, equipment, staff or failures…" aria-label="question" />
        <button className="btn" disabled={busy || q.trim().length < 3}>Ask</button>
      </form>
    </>
  );
}
