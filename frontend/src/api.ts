export type Ward = {
  id: string; code: string; name: string; unit_type: string; floor: number; inpatient: boolean;
  beds: number; occupied: number; available: number; occupancy: number; staff_on_duty: number; nurses_on_duty: number;
};
export type Summary = {
  nodes: number; relationships: number; by_label: Record<string, number>;
  inpatient_beds: number; occupied_beds: number; occupancy: number;
};
export type SearchHit = { id: string; label: string; name: string; kind: string; score: number };
export type Patient = { id: string; acuity: number; reason: string; age: number; bed: string; ward: string };
export type Substitute = {
  kind: string; ward: string; needed: number; available_elsewhere: number;
  options: { id: string; ward: string; floor: number; distance: number }[];
};
export type Impact = {
  failed: { id: string; name: string; kind: string }[];
  category: string; severity: "low" | "moderate" | "high" | "critical";
  counts: { assets: number; infrastructure: number; beds: number; patients: number; wards: number; assets_on_battery: number };
  modes: Record<string, number>;
  wards: { ward: string; beds: number; patients: number; high_acuity: number; assets: number }[];
  patients: Patient[]; beds: string[]; min_battery_minutes: number | null; substitutes: Substitute[]; query_ms: number;
};
export type Kpis = {
  arrivals: number; arrivals_per_day: number; discharges: number; occupancy: number;
  occupancy_by_unit: Record<string, number>; ed_boarding_h: { mean: number; p50: number; p90: number };
  ed_boarding_over_4h_share: number; los_days: { mean: number; median: number; p90: number };
  off_service_share: number; peak_boarding: number;
};
export type CensusRow = { t_h: number; ED_boarding: number; offline_beds: number; [unit: string]: number };
export type SimRun = { kpis: Kpis; census: CensusRow[]; runtime_ms: number };
export type SimResponse = {
  name: string; offline_beds: number; scenario: SimRun; baseline?: SimRun;
  delta?: Record<string, number>; run_id: number | null;
};
export type AskResponse = {
  answer: string; citations: string[]; provider: string; grounded: boolean; latency_ms: number;
  tool_calls: { tool: string; args: Record<string, unknown>; citations: string[] }[];
};
export type PlanBed = { id: string; name: string; isolation: boolean; inpatient: boolean;
  patient: { id: string; acuity: number; age: number; reason: string } | null };
export type PlanRoom = { id: string; kind: string; size: number; beds: PlanBed[] };
export type PlanWard = { id: string; code: string; name: string; unit_type: string; inpatient: boolean;
  rooms: PlanRoom[]; shared_assets: number };
export type FloorPlan = { id: string; name: string; level: number; wards: PlanWard[];
  infrastructure: { id: string; name: string; kind: string }[]; circuits: number };
export type FloorSummary = { level: number; name: string; wards: string[]; beds: number; occupied: number };
export type NodeInfo = Record<string, unknown> & { id: string; name: string; label: string; kind: string };
export type Neighborhood = {
  nodes: { id: string; name: string; kind: string; label: string }[];
  links: { source: string; target: string; type: string }[];
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "content-type": "application/json" }, ...init });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  summary: () => req<Summary>("/api/summary"),
  wards: () => req<Ward[]>("/api/wards"),
  search: (q: string) => req<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}`),
  infrastructure: () => req<{ id: string; name: string; kind: string; floor: number }[]>("/api/infrastructure"),
  impact: (ids: string[]) => req<Impact>("/api/impact", { method: "POST", body: JSON.stringify({ node_ids: ids }) }),
  simulate: (body: Record<string, unknown>) =>
    req<SimResponse>("/api/simulations", { method: "POST", body: JSON.stringify(body) }),
  ask: (question: string) =>
    req<AskResponse>("/api/assistant/ask", { method: "POST", body: JSON.stringify({ question }) }),
  floors: () => req<FloorSummary[]>("/api/floors"),
  floorPlan: (level: number) => req<FloorPlan>(`/api/floors/${level}`),
  node: (id: string) => req<NodeInfo>(`/api/nodes/${encodeURIComponent(id)}`),
  neighborhood: (id: string) => req<Neighborhood>(`/api/nodes/${encodeURIComponent(id)}/neighborhood`),
};
