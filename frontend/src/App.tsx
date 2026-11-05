import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, type SearchHit } from "./api";
import { NodePanel } from "./components";
import Assistant from "./pages/Assistant";
import Impact from "./pages/Impact";
import Overview from "./pages/Overview";
import Simulation from "./pages/Simulation";

function Explorer({ id, setId }: { id: string | null; setId: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  useEffect(() => {
    if (q.length < 2) { setHits([]); return; }
    const t = setTimeout(() => api.search(q).then(setHits).catch(() => setHits([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <>
      <h1>Graph explorer</h1>
      <p className="lede">Search any ward, bed, asset, infrastructure node, staff member or patient and walk its relationships.</p>
      <div className="grid two">
        <div className="card">
          <input style={{ width: "100%" }} placeholder="e.g. generator, MICU, ventilator 12" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="search" />
          <div className="list" style={{ marginTop: 10 }}>
            {hits.map((h) => (
              <div key={h.id} style={{ padding: "6px 4px", cursor: "pointer", borderBottom: "1px solid var(--grid)" }}
                onClick={() => setId(h.id)}>
                {h.name} <span className="muted small">{h.label} · {h.id}</span>
              </div>
            ))}
          </div>
        </div>
        <NodePanel id={id} onNavigate={setId} />
      </div>
    </>
  );
}

export default function App() {
  const [inspect, setInspect] = useState<string | null>(null);
  const navigate = useNavigate();
  const open = (id: string) => { setInspect(id); navigate("/explore"); };
  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">CareTwin</div>
        <div className="brand-sub">Hospital digital twin</div>
        <NavLink to="/" end>Overview</NavLink>
        <NavLink to="/impact">Failure impact</NavLink>
        <NavLink to="/simulation">Simulation</NavLink>
        <NavLink to="/assistant">Assistant</NavLink>
        <NavLink to="/explore">Graph explorer</NavLink>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/impact" element={<Impact onInspect={open} />} />
          <Route path="/simulation" element={<Simulation />} />
          <Route path="/assistant" element={<Assistant onInspect={open} />} />
          <Route path="/explore" element={<Explorer id={inspect} setId={setInspect} />} />
        </Routes>
      </main>
    </div>
  );
}
