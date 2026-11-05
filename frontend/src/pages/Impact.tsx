import { useEffect, useState } from "react";
import { api, type Impact as ImpactT, type SimResponse } from "../api";
import { Chip, SeverityBadge, StatTile } from "../components";
import { CensusChart } from "./Simulation";

type Infra = { id: string; name: string; kind: string; floor: number };

export default function Impact({ onInspect }: { onInspect: (id: string) => void }) {
  const [infra, setInfra] = useState<Infra[]>([]);
  const [selected, setSelected] = useState<string[]>(["ZV-O2-MICU"]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<{ id: string; name: string; label: string }[]>([]);
  const [res, setRes] = useState<ImpactT | null>(null);
  const [sim, setSim] = useState<SimResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.infrastructure().then(setInfra).catch(() => {}); }, []);
  useEffect(() => {
    if (query.length < 2) { setHits([]); return; }
    const t = setTimeout(() => api.search(query).then((h) => setHits(h.filter((x) =>
      ["Asset", "Infrastructure"].includes(x.label)))).catch(() => {}), 200);
    return () => clearTimeout(t);
  }, [query]);

  const run = async () => {
    setBusy(true); setErr(null); setSim(null);
    try { setRes(await api.impact(selected)); } catch (e) { setErr(String(e)); }
    setBusy(false);
  };
  const simulate = async () => {
    setBusy(true); setErr(null);
    try {
      setSim(await api.simulate({ name: `outage ${selected.join("+")}`, days: 14, failed_node_ids: selected,
        outage_start_h: 24, outage_duration_h: 72 }));
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };
  const add = (id: string) => { setSelected((s) => (s.includes(id) ? s : [...s, id])); setQuery(""); setHits([]); };

  return (
    <>
      <h1>Failure impact</h1>
      <p className="lede">Pick equipment or infrastructure, see what breaks downstream, then replay the outage in the simulator.</p>
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row" style={{ marginBottom: 10 }}>
          <select aria-label="infrastructure" value="" onChange={(e) => e.target.value && add(e.target.value)}>
            <option value="">Add infrastructure…</option>
            {infra.map((i) => <option key={i.id} value={i.id}>{i.name} ({i.id})</option>)}
          </select>
          <div style={{ position: "relative" }}>
            <input placeholder="…or search assets (e.g. ventilator 3)" value={query}
              onChange={(e) => setQuery(e.target.value)} style={{ width: 280 }} />
            {hits.length > 0 && (
              <div className="card" style={{ position: "absolute", zIndex: 5, top: 40, width: 320, padding: 6 }}>
                {hits.map((h) => (
                  <div key={h.id} style={{ padding: 4, cursor: "pointer" }} onClick={() => add(h.id)}>
                    {h.name} <span className="muted small">{h.id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="row">
          <span className="muted small">Failing:</span>
          {selected.map((id) => (
            <button key={id} className="chip" onClick={() => setSelected((s) => s.filter((x) => x !== id))}>
              {id} ✕
            </button>
          ))}
          <button className="btn" onClick={run} disabled={!selected.length || busy}>Analyse impact</button>
          <button className="btn ghost" onClick={simulate} disabled={!selected.length || busy}>Simulate 72 h outage</button>
        </div>
        {err && <p className="err">{err}</p>}
      </div>

      {res && (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <SeverityBadge severity={res.severity} />
            <span className="muted small">{res.category} failure · query {res.query_ms} ms</span>
          </div>
          <div className="grid tiles">
            <StatTile label="Patients affected" value={res.counts.patients}
              sub={`${res.patients.filter((p) => p.acuity >= 4).length} high acuity`} />
            <StatTile label="Beds affected" value={res.counts.beds} sub={`${res.counts.wards} wards`} />
            <StatTile label="Assets affected" value={res.counts.assets}
              sub={Object.entries(res.modes).filter(([, n]) => n).map(([m, n]) => `${n} ${m}`).join(" · ")} />
            <StatTile label="Shortest battery runway" value={res.min_battery_minutes ? `${res.min_battery_minutes} min` : "—"}
              sub={`${res.counts.assets_on_battery} devices on battery`} />
          </div>
          <div className="grid two">
            <div className="card">
              <h2>Wards</h2>
              <table>
                <thead><tr><th>Ward</th><th className="num">Beds</th><th className="num">Patients</th>
                  <th className="num">High acuity</th><th className="num">Assets</th></tr></thead>
                <tbody>{res.wards.map((w) => (
                  <tr key={w.ward}><td><Chip id={`WARD-${w.ward}`} onClick={onInspect} /></td><td className="num">{w.beds}</td>
                    <td className="num">{w.patients}</td><td className="num">{w.high_acuity}</td>
                    <td className="num">{w.assets}</td></tr>))}
                </tbody>
              </table>
            </div>
            <div className="card">
              <h2>Replacement equipment</h2>
              {res.substitutes.length === 0 && <p className="muted">No replacement needed (network-only or battery-covered impact).</p>}
              <table>
                <tbody>{res.substitutes.slice(0, 8).map((s) => (
                  <tr key={s.kind + s.ward}><td>{s.needed}× {s.kind.replace(/_/g, " ")} <span className="muted small">for {s.ward}</span></td>
                    <td className="num">{s.available_elsewhere} spare</td>
                    <td>{s.options.slice(0, 2).map((o) => <Chip key={o.id} id={o.id} onClick={onInspect} />)}</td></tr>))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <h2>Patients on affected beds (highest acuity first)</h2>
            <div className="list">
              <table>
                <thead><tr><th>Patient</th><th>Bed</th><th className="num">Acuity</th><th className="num">Age</th><th>Reason</th></tr></thead>
                <tbody>{res.patients.slice(0, 60).map((p) => (
                  <tr key={p.id}><td><Chip id={p.id} onClick={onInspect} /></td><td>{p.bed}</td>
                    <td className="num">{p.acuity}</td><td className="num">{p.age}</td><td className="small">{p.reason}</td></tr>))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
      {sim && sim.baseline && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Outage replay: {sim.offline_beds} beds offline for 72 h</h2>
          <p className="small muted">
            ED boarding p90 {sim.baseline.kpis.ed_boarding_h.p90} h → {sim.scenario.kpis.ed_boarding_h.p90} h ·
            peak boarders {sim.baseline.kpis.peak_boarding} → {sim.scenario.kpis.peak_boarding} ·
            off-service {(sim.baseline.kpis.off_service_share * 100).toFixed(1)}% → {(sim.scenario.kpis.off_service_share * 100).toFixed(1)}%
          </p>
          <CensusChart baseline={sim.baseline.census} scenario={sim.scenario.census} metric="ED_boarding"
            title="Patients boarding (waiting for a bed)" />
        </div>
      )}
    </>
  );
}
