import { useEffect, useState } from "react";
import { api, type Summary, type Ward } from "../api";
import { OccupancyBar, StatTile } from "../components";

export default function Overview() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [wards, setWards] = useState<Ward[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([api.summary(), api.wards()]).then(([s, w]) => { setSummary(s); setWards(w); })
      .catch((e) => setErr(String(e)));
  }, []);
  if (err) return <p className="err">Could not reach the API: {err}</p>;
  if (!summary) return <p className="muted">Loading twin…</p>;
  const inpatient = wards.filter((w) => w.inpatient);
  return (
    <>
      <h1>Hospital overview</h1>
      <p className="lede">Live state of the 300-bed twin: census from the patient-flow simulation, structure from the graph.</p>
      <div className="grid tiles">
        <StatTile label="Inpatient occupancy" value={`${Math.round(summary.occupancy * 100)}%`}
          sub={`${summary.occupied_beds} of ${summary.inpatient_beds} beds`} />
        <StatTile label="Free inpatient beds" value={summary.inpatient_beds - summary.occupied_beds} />
        <StatTile label="Graph nodes" value={summary.nodes.toLocaleString()}
          sub={`${summary.relationships.toLocaleString()} relationships`} />
        <StatTile label="Staff / assets" value={`${summary.by_label.Staff ?? 0} / ${summary.by_label.Asset ?? 0}`} />
      </div>
      <div className="card">
        <h2>Inpatient wards</h2>
        <table>
          <thead>
            <tr><th>Ward</th><th>Unit</th><th className="num">Floor</th><th className="num">Occupied</th>
              <th className="num">Free</th><th>Occupancy</th><th className="num">RNs on duty</th></tr>
          </thead>
          <tbody>
            {inpatient.map((w) => (
              <tr key={w.id}>
                <td>{w.name} <span className="muted small">{w.code}</span></td>
                <td className="muted">{w.unit_type}</td>
                <td className="num">{w.floor}</td>
                <td className="num">{w.occupied}/{w.beds}</td>
                <td className="num">{w.available}</td>
                <td><div className="row"><OccupancyBar value={w.occupancy} />
                  <span className="small">{Math.round(w.occupancy * 100)}%</span></div></td>
                <td className="num">{w.nurses_on_duty}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
