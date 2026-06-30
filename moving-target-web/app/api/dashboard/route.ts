import { NextResponse } from "next/server";
import { auth } from "../../../auth";

const mockPayload = {
  samples: [
    {
      sequence: 1,
      timestamp_utc: new Date().toISOString(),
      latency_summary: { min_ms: 42, avg_ms: 88, max_ms: 141 },
      location: { lat: 52.52, lon: 13.405, source: "mock", accuracy_m: 18 },
      wifi: { ssid: "local mock" },
      router_geo: {
        access_point_name: "192.168.1.1",
        first_hop: { hop: 1, host: "192.168.1.1", ips: ["192.168.1.1"] }
      },
      motion: { speed_kmh: 0 },
      radio: {}
    }
  ],
  status: { collection_enabled: true, source: "mock" },
  settings: {},
  route_tracks: []
};

export async function GET() {
  const session = await auth();
  if (!session?.user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const upstream = process.env.MOVING_TARGET_API_URL;
  if (!upstream) return NextResponse.json(mockPayload);

  const res = await fetch(upstream.replace(/\/$/, "") + "/api/samples", {
    headers: process.env.MOVING_TARGET_API_TOKEN
      ? { Authorization: `Bearer ${process.env.MOVING_TARGET_API_TOKEN}` }
      : {},
    cache: "no-store"
  });
  if (!res.ok) {
    return NextResponse.json({ error: `upstream failed: ${res.status}` }, { status: 502 });
  }
  return NextResponse.json(await res.json());
}
