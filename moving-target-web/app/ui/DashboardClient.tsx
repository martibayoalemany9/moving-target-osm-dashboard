"use client";

import { useEffect, useMemo, useState } from "react";

type Sample = {
  sequence?: number;
  timestamp_utc?: string;
  latency_summary?: { min_ms?: number; avg_ms?: number; max_ms?: number };
  location?: { lat?: number; lon?: number; source?: string; accuracy_m?: number };
  wifi?: { ssid?: string };
  router_geo?: { access_point_name?: string; first_hop?: { host?: string; ips?: string[] } };
  motion?: { speed_kmh?: number };
};

type Payload = {
  samples: Sample[];
  status?: Record<string, unknown>;
};

function accessPoint(sample?: Sample) {
  return sample?.router_geo?.access_point_name
    || sample?.router_geo?.first_hop?.host
    || sample?.router_geo?.first_hop?.ips?.[0]
    || "waiting for traceroute hop 1";
}

export default function DashboardClient() {
  const [payload, setPayload] = useState<Payload>({ samples: [] });
  const [error, setError] = useState("");

  async function load() {
    try {
      const res = await fetch("/api/dashboard", { cache: "no-store" });
      if (!res.ok) throw new Error(`dashboard API ${res.status}`);
      setPayload(await res.json());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const latest = payload.samples.at(-1);
  const points = useMemo(() => payload.samples.filter(s => s.location?.lat && s.location?.lon), [payload.samples]);

  return (
    <main className="dashboard">
      <section className="metrics">
        <Metric label="Samples" value={String(payload.samples.length)} />
        <Metric label="Avg latency" value={latest?.latency_summary?.avg_ms ? `${latest.latency_summary.avg_ms} ms` : "--"} />
        <Metric label="Speed" value={latest?.motion?.speed_kmh ? `${latest.motion.speed_kmh} km/h` : "--"} />
        <Metric label="Access point" value={accessPoint(latest)} />
        <Metric label="GPS latitude" value={latest?.location?.lat?.toFixed(5) || "--"} />
        <Metric label="GPS longitude" value={latest?.location?.lon?.toFixed(5) || "--"} />
      </section>

      {error && <div className="error">{error}</div>}

      <section className="content-grid">
        <div className="map-pane">
          <div className="map-placeholder">
            <strong>Map surface</strong>
            <span>{points.length} geolocated sample{points.length === 1 ? "" : "s"}</span>
            <small>Set `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` to replace this with Google Maps rendering.</small>
          </div>
        </div>
        <div className="details">
          <h2>Latest Sample</h2>
          <dl>
            <dt>Time</dt><dd>{latest?.timestamp_utc || "--"}</dd>
            <dt>Source</dt><dd>{latest?.location?.source || "--"}</dd>
            <dt>Accuracy</dt><dd>{latest?.location?.accuracy_m ? `${latest.location.accuracy_m} m` : "--"}</dd>
            <dt>Status</dt><dd>{JSON.stringify(payload.status || {})}</dd>
          </dl>
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
