import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { SeverityBadge } from "./components";
import { renderAnswer } from "./pages/Assistant";

const summary = { nodes: 4327, relationships: 11414, by_label: { Staff: 1671, Asset: 1574 }, inpatient_beds: 300,
  occupied_beds: 261, occupancy: 0.87 };
const wards = [{ id: "WARD-MICU", code: "MICU", name: "Medical ICU", unit_type: "ICU", floor: 2, inpatient: true, beds: 20,
  occupied: 15, available: 5, occupancy: 0.75, staff_on_duty: 20, nurses_on_duty: 14 }];

function mockFetch(routes: Record<string, unknown>) {
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
    const key = Object.keys(routes).find((k) => String(url).startsWith(k));
    return new Response(JSON.stringify(key ? routes[key] : {}), { status: key ? 200 : 404 });
  }) as typeof fetch;
}

describe("CareTwin UI", () => {
  it("renders overview tiles and ward table", async () => {
    mockFetch({ "/api/summary": summary, "/api/wards": wards });
    render(<MemoryRouter><App /></MemoryRouter>);
    expect(await screen.findByText("87%")).toBeInTheDocument();
    expect(screen.getByText("Medical ICU")).toBeInTheDocument();
    expect(screen.getByText("15/20")).toBeInTheDocument();
  });

  it("shows an error when the API is down", async () => {
    globalThis.fetch = vi.fn(async () => new Response("{}", { status: 503 })) as typeof fetch;
    render(<MemoryRouter><App /></MemoryRouter>);
    expect(await screen.findByText(/Could not reach the API/)).toBeInTheDocument();
  });

  it("asks the assistant and renders citation chips", async () => {
    mockFetch({ "/api/assistant/ask": { answer: "MICU has 5 free beds [WARD-MICU].", citations: ["WARD-MICU"],
      provider: "offline", grounded: true, latency_ms: 12, tool_calls: [{ tool: "get_ward", args: {}, citations: [] }] } });
    render(<MemoryRouter initialEntries={["/assistant"]}><App /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("question"), { target: { value: "free beds in micu?" } });
    fireEvent.click(screen.getByText("Ask"));
    await waitFor(() => expect(screen.getByText("WARD-MICU")).toBeInTheDocument());
    expect(screen.getByText(/citations verified/)).toBeInTheDocument();
  });

  it("severity badge always carries a text label", () => {
    render(<SeverityBadge severity="critical" />);
    expect(screen.getByLabelText("severity critical")).toHaveTextContent("critical");
  });

  it("renderAnswer turns bracketed ids into chips", () => {
    const onCite = vi.fn();
    render(<div>{renderAnswer("A [GEN-1] and [ZV-O2-MICU].", onCite)}</div>);
    fireEvent.click(screen.getByText("GEN-1"));
    expect(onCite).toHaveBeenCalledWith("GEN-1");
    expect(screen.getByText("ZV-O2-MICU")).toBeInTheDocument();
  });
});

describe("floor plan layout", () => {
  it("places rooms on both sides of the corridor without overlap", async () => {
    const { layoutFloor } = await import("./pages/FloorPlan");
    const bed = (id: string) => ({ id, name: id, isolation: false, inpatient: true, patient: null });
    const rooms = Array.from({ length: 6 }, (_, i) => ({ id: `R${i}`, kind: "patient_room", size: 1 + (i % 2), beds: [bed(`B${i}`)] }));
    const out = layoutFloor([{ id: "WARD-X", code: "X", name: "Ward X", unit_type: "MED", inpatient: true, rooms, shared_assets: 0 }]);
    const placed = out.wards[0].rooms;
    expect(placed).toHaveLength(6);
    expect(new Set(placed.map((r) => r.y)).size).toBe(2);
    const top = placed.filter((r) => r.y === placed[0].y).sort((a, b) => a.x - b.x);
    for (let i = 1; i < top.length; i++) expect(top[i].x).toBeGreaterThanOrEqual(top[i - 1].x + top[i - 1].w);
    expect(out.width).toBeGreaterThanOrEqual(out.wards[0].w);
  });

  it("wraps wide floors onto a new row", async () => {
    const { layoutFloor } = await import("./pages/FloorPlan");
    const bed = { id: "b", name: "b", isolation: false, inpatient: true, patient: null };
    const ward = (c: string) => ({ id: c, code: c, name: c, unit_type: "MED", inpatient: true, shared_assets: 0,
      rooms: Array.from({ length: 30 }, (_, i) => ({ id: `${c}${i}`, kind: "patient_room", size: 2, beds: [bed, bed] })) });
    const out = layoutFloor([ward("A"), ward("B")]);
    expect(out.wards[1].y).toBeGreaterThan(out.wards[0].y);
  });
});
