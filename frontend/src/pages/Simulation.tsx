import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type CensusRow, type SimResponse } from "../api";
import { StatTile } from "../components";

const UNITS = ["ICU", "STEPDOWN", "CARD", "MED", "NEURO", "ONC", "SURG", "TRANSPLANT", "PSYCH"];

function totalOccupied(r: CensusRow) {
  return UNITS.reduce((s, u) => s + (r[u] ?? 0), 0);
}

export function CensusChart({ baseline, scenario, metric, title }:
  { baseline?: CensusRow[]; scenario: CensusRow[]; metric: "total" | "ED_boarding"; title: string }) {
  const pick = (r: CensusRow) => (metric === "total" ? totalOccupied(r) : r.ED_boarding);
  const t0 = scenario[0]?.t_h ?? 0;
  const data = scenario.map((r, i) => ({
    day: +((r.t_h - t0) / 24).toFixed(2), scenario: pick(r),
    ...(baseline && baseline[i] ? { baseline: pick(baseline[i]) } : {}),
  }));
  return (
    <figure style={{ margin: 0 }}>
      <figcaption className="small muted" style={{ marginBottom: 6 }}>{title}</figcaption>
      <div style={{ width: "100%", height: 260 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="day" stroke="var(--axis)" tick={{ fill: "var(--muted)", fontSize: 12 }}
              tickFormatter={(d) => `d${Math.round(d)}`} type="number" domain={["dataMin", "dataMax"]} />
            <YAxis stroke="var(--axis)" tick={{ fill: "var(--muted)", fontSize: 12 }} allowDecimals={false} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--grid)", borderRadius: 8 }}
              labelFormatter={(d) => `Day ${d}`} />
            {baseline && <Line type="linear" isAnimationActive={false} dataKey="baseline" name="Baseline" stroke="var(--series-1)"
              strokeWidth={2} dot={false} />}
            <Line type="linear" isAnimationActive={false} dataKey="scenario" name={baseline ? "Scenario" : "Simulated"}
              stroke={baseline ? "var(--series-2)" : "var(--series-1)"} strokeWidth={2} dot={false} />
            {baseline && <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: "var(--ink-2)" }} />}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

export default function Simulation() {
  const [days, setDays] = useState(14);
  const [surge, setSurge] = useState(1.5);
  const [surgeDays, setSurgeDays] = useState(3);
  const [seed, setSeed] = useState(42);
  const [res, setRes] = useState<SimResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true); setErr(null);
    try {
      setRes(await api.simulate({ name: `surge x${surge}`, days, seed, surge_multiplier: surge, surge_start_h: 24,
        surge_end_h: 24 + surgeDays * 24 }));
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };
  const k = res?.scenario.kpis;
  const b = res?.baseline?.kpis;
  return (
    <>
      <h1>Patient-flow simulation</h1>
      <p className="lede">Discrete-event model: Synthea case mix, MIMIC-IV-calibrated stays and transfers, 300 real beds.</p>
      <div className="card row" style={{ marginBottom: 16 }}>
        <label>Days <input type="number" min={1} max={60} value={days} onChange={(e) => setDays(+e.target.value)} style={{ width: 70 }} /></label>
        <label>Arrival surge × <input type="number" step={0.1} min={0.2} max={3} value={surge}
          onChange={(e) => setSurge(+e.target.value)} style={{ width: 70 }} /></label>
        <label>for <input type="number" min={0} max={30} value={surgeDays} onChange={(e) => setSurgeDays(+e.target.value)}
          style={{ width: 60 }} /> days</label>
        <label>Seed <input type="number" value={seed} onChange={(e) => setSeed(+e.target.value)} style={{ width: 80 }} /></label>
        <button className="btn" onClick={run} disabled={busy}>{busy ? "Running…" : "Run"}</button>
        {err && <span className="err">{err}</span>}
      </div>
      {k && (
        <>
          <div className="grid tiles">
            <StatTile label="Mean occupancy" value={`${Math.round(k.occupancy * 100)}%`}
              sub={b ? `baseline ${Math.round(b.occupancy * 100)}%` : undefined} />
            <StatTile label="ED boarding p90" value={`${k.ed_boarding_h.p90} h`} sub={b ? `baseline ${b.ed_boarding_h.p90} h` : undefined} />
            <StatTile label="Peak boarders" value={k.peak_boarding} sub={b ? `baseline ${b.peak_boarding}` : undefined} />
            <StatTile label="Length of stay (median)" value={`${k.los_days.median.toFixed(1)} d`} sub={`p90 ${k.los_days.p90.toFixed(1)} d`} />
            <StatTile label="Arrivals / day" value={k.arrivals_per_day} sub={`${res!.scenario.runtime_ms.toFixed(0)} ms runtime`} />
          </div>
          <div className="grid two">
            <div className="card"><CensusChart baseline={res!.baseline?.census} scenario={res!.scenario.census}
              metric="total" title="Occupied inpatient beds" /></div>
            <div className="card"><CensusChart baseline={res!.baseline?.census} scenario={res!.scenario.census}
              metric="ED_boarding" title="Patients boarding (waiting for a bed)" /></div>
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <h2>Occupancy by unit type</h2>
            <table>
              <thead><tr><th>Unit</th><th className="num">Scenario</th>{b && <th className="num">Baseline</th>}</tr></thead>
              <tbody>{Object.entries(k.occupancy_by_unit).map(([u, v]) => (
                <tr key={u}><td>{u}</td><td className="num">{(v * 100).toFixed(1)}%</td>
                  {b && <td className="num">{((b.occupancy_by_unit[u] ?? 0) * 100).toFixed(1)}%</td>}</tr>))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
