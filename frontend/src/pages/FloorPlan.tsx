import { useEffect, useMemo, useRef, useState } from "react";
import { api, type FloorPlan as Plan, type FloorSummary, type Impact, type PlanBed, type PlanWard } from "../api";
import { SeverityBadge } from "../components";

// ---- geometry (SVG user units) ---------------------------------------------------
const ROOM_H = 46;
const CORRIDOR = 24;
const HEADER = 30;
const PAD = 12;
const WING_GAP = 28;
const MAX_ROW = 1180;
const roomW = (size: number) => (size >= 2 ? 56 : 34);

type PlacedRoom = { id: string; x: number; y: number; w: number; beds: PlanBed[]; kind: string };
type PlacedWard = { ward: PlanWard; x: number; y: number; w: number; h: number; rooms: PlacedRoom[] };

/** Double-loaded corridor: rooms alternate above and below a central corridor, as on real inpatient units. */
export function layoutFloor(wards: PlanWard[]): { wards: PlacedWard[]; width: number; height: number } {
  const wingH = HEADER + ROOM_H + CORRIDOR + ROOM_H + PAD;
  const placed: PlacedWard[] = [];
  let x = 0, y = 0, rowW = 0;
  for (const ward of wards) {
    const top = ward.rooms.filter((_, i) => i % 2 === 0);
    const bottom = ward.rooms.filter((_, i) => i % 2 === 1);
    const span = (rs: typeof top) => rs.reduce((s, r) => s + roomW(r.size) + 4, 0);
    const nBeds = ward.rooms.reduce((n, r) => n + r.beds.length, 0);
    const meta = ward.rooms.length ? (ward.inpatient ? `${nBeds}/${nBeds} occupied` : `${nBeds} bays · not in census`) : "";
    const headerW = ward.name.length * 7.4 + meta.length * 5.6 + 2 * PAD + 16; // ~13px bold name + 11px meta
    const w = Math.max(190, headerW, PAD * 2 + Math.max(span(top), span(bottom)));
    if (x > 0 && x + w > MAX_ROW) { x = 0; y += wingH + WING_GAP; }
    const rooms: PlacedRoom[] = [];
    const place = (rs: typeof top, ry: number) => {
      let rx = x + PAD;
      for (const r of rs) { rooms.push({ id: r.id, x: rx, y: ry, w: roomW(r.size), beds: r.beds, kind: r.kind }); rx += roomW(r.size) + 4; }
    };
    place(top, y + HEADER);
    place(bottom, y + HEADER + ROOM_H + CORRIDOR);
    placed.push({ ward, x, y, w, h: wingH, rooms });
    x += w + WING_GAP;
    rowW = Math.max(rowW, x - WING_GAP);
  }
  return { wards: placed, width: Math.max(rowW, 300), height: placed.length ? y + wingH : 0 };
}

const INFRA_LABEL: Record<string, string> = {
  power_panel: "Power panels", o2_zone_valve: "Oxygen zone valves", air_zone_valve: "Medical-air zone valves",
  network_switch: "Network switches", generator: "Generators", transfer_switch: "Transfer switches", ups: "UPS",
  core_switch: "Core network", utility_feed: "Utility feed", oxygen_source: "Oxygen supply",
  oxygen_manifold: "Oxygen supply", medical_air_plant: "Medical-air plant",
};
const INFRA_ICON: Record<string, string> = {
  power_panel: "⚡", generator: "⚡", transfer_switch: "⚡", ups: "⚡", utility_feed: "⚡",
  o2_zone_valve: "O₂", oxygen_source: "O₂", oxygen_manifold: "O₂", air_zone_valve: "Air", medical_air_plant: "Air",
  network_switch: "⇄", core_switch: "⇄",
};

function bedFill(bed: PlanBed) {
  if (!bed.patient) return "var(--surface)";
  return `var(--acuity-${Math.min(5, Math.max(1, bed.patient.acuity))})`;
}

export default function FloorPlanPage({ onInspect }: { onInspect: (id: string) => void }) {
  const [floors, setFloors] = useState<FloorSummary[]>([]);
  const [level, setLevel] = useState(2);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [failId, setFailId] = useState("");
  const [hover, setHover] = useState<{ bed: PlanBed; ward: string; x: number; y: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => { api.floors().then(setFloors).catch((e) => setErr(String(e))); }, []);
  useEffect(() => {
    setPlan(null);
    api.floorPlan(level).then(setPlan).catch((e) => setErr(String(e)));
  }, [level]);

  const layout = useMemo(() => layoutFloor(plan?.wards ?? []), [plan]);
  const affected = useMemo(() => new Set(impact?.beds ?? []), [impact]);
  const affectedHere = plan ? plan.wards.flatMap((w) => w.rooms.flatMap((r) => r.beds)).filter((b) => affected.has(b.id)) : [];

  const wardFloor = useMemo(() => new Map(floors.flatMap((f) => f.wards.map((w) => [w, f.level] as const))), [floors]);
  const otherWards = useMemo(() => {
    const here = new Set(plan?.wards.map((w) => w.code) ?? []);
    const codes = [...new Set((impact?.beds ?? []).map((b) => b.split("-")[1]))].filter((c) => !here.has(c));
    return codes.map((c) => [c, wardFloor.get(c)] as const);
  }, [impact, plan, wardFloor]);

  const runImpact = async (id: string) => {
    if (!id.trim()) return;
    setErr(null);
    try { setImpact(await api.impact([id.trim().toUpperCase()])); setFailId(id.trim().toUpperCase()); }
    catch (e) { setErr(String(e)); setImpact(null); }
  };

  const groups = (plan?.infrastructure ?? []).reduce<Record<string, Plan["infrastructure"]>>((acc, n) => {
    const k = INFRA_LABEL[n.kind] ?? n.kind; (acc[k] ||= []).push(n); return acc;
  }, {});

  return (
    <>
      <h1>Floor plan</h1>
      <p className="lede">A 2D map of the twin, floor by floor. Beds are shaded by patient acuity; pick any panel,
        valve, switch or device to see where its failure lands.</p>

      <div className="row floor-tabs" role="tablist" aria-label="floors" style={{ marginBottom: 12 }}>
        {floors.map((f) => (
          <button key={f.level} role="tab" aria-selected={f.level === level}
            className={`btn ${f.level === level ? "" : "ghost"}`} onClick={() => setLevel(f.level)}
            title={f.name}>
            {f.level === 0 ? "Plant" : `L${f.level}`}
            {f.beds > 0 && <span className="small" style={{ opacity: 0.8 }}> · {f.occupied}/{f.beds}</span>}
          </button>
        ))}
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row">
          <strong>{plan?.name ?? "Loading…"}</strong>
          <span className="muted small">{plan ? `${plan.circuits} branch circuits on this floor` : ""}</span>
          <span style={{ flex: 1 }} />
          <input aria-label="failing node" placeholder="Fail a node, e.g. PNL-2CR" value={failId}
            onChange={(e) => setFailId(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runImpact(failId)}
            style={{ width: 220 }} />
          <button className="btn" onClick={() => runImpact(failId)} disabled={!failId.trim()}>Show impact</button>
          {impact && <button className="btn ghost" onClick={() => { setImpact(null); setFailId(""); }}>Clear</button>}
        </div>
        {impact && (
          <div className="row small" style={{ marginTop: 10 }}>
            <SeverityBadge severity={impact.severity} />
            <span>{impact.failed.map((f) => f.name).join(", ")}: {impact.counts.beds} beds and {impact.counts.patients} patients
              hospital-wide; <strong>{affectedHere.length}</strong> affected beds on this floor.</span>
            {otherWards.length > 0 && (
              <span className="row" style={{ gap: 4 }}>
                <span className="muted">Also affected:</span>
                {otherWards.map(([code, lvl]) => (
                  <button key={code} className="chip" onClick={() => lvl !== undefined && setLevel(lvl)}
                    title={lvl !== undefined ? `Go to level ${lvl}` : undefined}>
                    {code}{lvl !== undefined ? ` · L${lvl}` : ""}
                  </button>
                ))}
              </span>
            )}
          </div>
        )}
        {err && <p className="err">{err}</p>}
      </div>

      {plan && plan.wards.length > 0 && (
        <div className="card plan-card" ref={wrap} style={{ position: "relative" }}>
          <svg viewBox={`-4 -4 ${layout.width + 8} ${layout.height + 8}`} width="100%"
            style={{ minWidth: Math.min(layout.width, 760), display: "block" }} role="img"
            aria-label={`Floor plan of ${plan.name}`} onMouseLeave={() => setHover(null)}>
            {layout.wards.map(({ ward, x, y, w, h, rooms }) => (
              <g key={ward.id}>
                <rect x={x} y={y} width={w} height={h} rx={8} fill="var(--surface-2)" stroke="var(--axis)" />
                <text x={x + PAD} y={y + 19} className="plan-ward">{ward.name}</text>
                {rooms.length > 0 && (
                  <text x={x + w - PAD} y={y + 19} textAnchor="end" className="plan-meta">
                    {ward.inpatient
                      ? `${rooms.flatMap((r) => r.beds).filter((b) => b.patient).length}/${rooms.flatMap((r) => r.beds).length} occupied`
                      : `${rooms.flatMap((r) => r.beds).length} bays · not in census`}
                  </text>
                )}
                {rooms.length > 0 && (
                  <>
                    <rect x={x + PAD} y={y + HEADER + ROOM_H + 4} width={w - 2 * PAD} height={CORRIDOR - 8} rx={3}
                      fill="var(--surface)" />
                    <text x={x + w / 2} y={y + HEADER + ROOM_H + CORRIDOR / 2 + 4} textAnchor="middle"
                      className="plan-corridor">corridor</text>
                  </>
                )}
                {rooms.length === 0 && (
                  <text x={x + PAD} y={y + HEADER + 40} className="plan-meta">
                    {ward.shared_assets} imaging devices
                  </text>
                )}
                {rooms.map((r) => (
                  <g key={r.id}>
                    <rect x={r.x} y={r.y} width={r.w} height={ROOM_H} rx={4} fill="var(--surface)"
                      stroke="var(--grid)" />
                    {r.beds.map((b, i) => {
                      const bw = 22, bh = 30;
                      const bx = r.x + (r.beds.length === 1 ? (r.w - bw) / 2 : 5 + i * (bw + 4));
                      const by = r.y + (ROOM_H - bh) / 2;
                      const hit = affected.has(b.id);
                      return (
                        <g key={b.id} className="plan-bed" tabIndex={0} role="button"
                          aria-label={`${b.name}${b.patient ? `, acuity ${b.patient.acuity}` : ", empty"}${hit ? ", affected" : ""}`}
                          onMouseEnter={(e) => {
                            const box = wrap.current?.getBoundingClientRect();
                            setHover({ bed: b, ward: ward.name, x: e.clientX - (box?.left ?? 0), y: e.clientY - (box?.top ?? 0) });
                          }}
                          onFocus={() => setHover({ bed: b, ward: ward.name, x: 20, y: 20 })}
                          onBlur={() => setHover(null)}
                          onClick={() => onInspect(b.patient?.id ?? b.id)}
                          onKeyDown={(e) => e.key === "Enter" && onInspect(b.patient?.id ?? b.id)}>
                          {/* larger invisible hit target than the mark */}
                          <rect x={bx - 2} y={by - 2} width={bw + 4} height={bh + 4} fill="transparent" />
                          <rect x={bx} y={by} width={bw} height={bh} rx={4} fill={bedFill(b)}
                            stroke={hit ? "var(--critical)" : b.patient ? "var(--surface)" : "var(--axis)"}
                            strokeWidth={hit ? 3 : 1.5} strokeDasharray={b.patient || hit ? undefined : "3 2"}
                          opacity={ward.inpatient ? 1 : 0.55} />
                          {b.isolation && <circle cx={bx + bw - 5} cy={by + 5} r={3.5} fill="var(--surface)" stroke="var(--ink)" strokeWidth={1.5} />}
                          {hit && <text x={bx + bw / 2} y={by + bh / 2 + 4} textAnchor="middle" className="plan-hit">▲</text>}
                        </g>
                      );
                    })}
                  </g>
                ))}
              </g>
            ))}
          </svg>
          {hover && (
            <div className="plan-tip" style={{ left: Math.min(hover.x + 14, (wrap.current?.clientWidth ?? 600) - 230), top: hover.y + 14 }}>
              <strong>{hover.bed.name}</strong> <span className="muted">· {hover.ward}</span>
              {hover.bed.patient ? (
                <div>{hover.bed.patient.id} · acuity {hover.bed.patient.acuity} · age {hover.bed.patient.age}
                  <div className="muted">{hover.bed.patient.reason}</div></div>
              ) : <div className="muted">{hover.bed.inpatient ? "Empty bed" : "ED/PACU/OR bay (not tracked by the census)"}</div>}
              {hover.bed.isolation && <div className="muted">Isolation room</div>}
              {affected.has(hover.bed.id) && <div style={{ color: "var(--critical)" }}>▲ Affected by {failId}</div>}
            </div>
          )}
          <div className="row small plan-legend" style={{ marginTop: 10 }}>
            <span className="muted">Acuity</span>
            {[1, 2, 3, 4, 5].map((a) => (
              <span key={a} className="row" style={{ gap: 4 }}>
                <span className="swatch" style={{ background: `var(--acuity-${a})` }} />{a}
              </span>
            ))}
            <span className="row" style={{ gap: 4 }}><span className="swatch empty" />Empty</span>
            <span className="row" style={{ gap: 4 }}><span className="swatch iso" />Isolation</span>
            <span className="row" style={{ gap: 4 }}><span className="swatch hit" />▲ Affected</span>
          </div>
        </div>
      )}
      {plan && plan.wards.length === 0 && (
        <div className="card muted">No clinical areas on this floor: it houses the hospital's central plant.</div>
      )}

      {plan && plan.infrastructure.length > 0 && (
        <div className="card" style={{ marginTop: 12 }}>
          <h2>Infrastructure on this floor</h2>
          <p className="small muted" style={{ marginTop: -6 }}>Click one to fail it and see the affected beds.</p>
          {Object.entries(groups).map(([label, nodes]) => (
            <div key={label} style={{ marginBottom: 8 }}>
              <div className="small muted">{label}</div>
              {nodes.map((n) => (
                <button key={n.id} className="chip" title={n.name} onClick={() => runImpact(n.id)}
                  aria-pressed={failId === n.id}>
                  <span aria-hidden>{INFRA_ICON[n.kind] ?? "•"}</span> {n.id}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
