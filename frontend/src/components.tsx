import { useEffect, useState } from "react";
import { api, type Neighborhood, type NodeInfo } from "./api";

export function StatTile({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

const SEV: Record<string, { color: string; icon: string }> = {
  low: { color: "var(--good)", icon: "✓" },
  moderate: { color: "var(--warning)", icon: "!" },
  high: { color: "var(--serious)", icon: "!!" },
  critical: { color: "var(--critical)", icon: "▲" },
};

export function SeverityBadge({ severity }: { severity: string }) {
  const s = SEV[severity] ?? SEV.low;
  return (
    <span className="badge" aria-label={`severity ${severity}`}>
      <span className="dot" style={{ background: s.color }} aria-hidden />
      <span aria-hidden>{s.icon}</span> {severity}
    </span>
  );
}

export function OccupancyBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <div className="bar" role="img" aria-label={`${Math.round(pct * 100)}% occupied`}>
      <span style={{ width: `${pct * 100}%` }} />
    </div>
  );
}

export function Chip({ id, onClick }: { id: string; onClick?: (id: string) => void }) {
  return (
    <button className="chip" onClick={() => onClick?.(id)} title="Inspect node">
      {id}
    </button>
  );
}

export function NodePanel({ id, onNavigate }: { id: string | null; onNavigate: (id: string) => void }) {
  const [node, setNode] = useState<NodeInfo | null>(null);
  const [nb, setNb] = useState<Neighborhood | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!id) return;
    setErr(null);
    Promise.all([api.node(id), api.neighborhood(id)])
      .then(([n, h]) => { setNode(n); setNb(h); })
      .catch((e) => setErr(String(e)));
  }, [id]);
  if (!id) return <div className="card muted">Select a citation or search result to inspect a node.</div>;
  if (err) return <div className="card err">{err}</div>;
  if (!node || !nb) return <div className="card muted">Loading {id}…</div>;
  const props = Object.entries(node).filter(([k]) => !["id", "name", "label"].includes(k));
  const groups = nb.links.reduce<Record<string, string[]>>((acc, l) => {
    const other = l.source === id ? l.target : l.source;
    const key = `${l.source === id ? "→" : "←"} ${l.type}`;
    (acc[key] ||= []).push(other);
    return acc;
  }, {});
  return (
    <div className="card">
      <div className="muted small">{node.label}</div>
      <h2 style={{ marginBottom: 4 }}>{node.name}</h2>
      <div className="small muted" style={{ marginBottom: 10 }}>{node.id}</div>
      <table>
        <tbody>
          {props.map(([k, v]) => (
            <tr key={k}><td className="muted">{k}</td><td>{String(v)}</td></tr>
          ))}
        </tbody>
      </table>
      <h2 style={{ marginTop: 16 }}>Relationships</h2>
      {Object.entries(groups).map(([k, ids]) => (
        <div key={k} style={{ marginBottom: 8 }}>
          <div className="small muted">{k} ({ids.length})</div>
          {ids.slice(0, 24).map((x) => <Chip key={x} id={x} onClick={onNavigate} />)}
          {ids.length > 24 && <span className="small muted">+{ids.length - 24} more</span>}
        </div>
      ))}
    </div>
  );
}
