package com.movingtarget.auto;

final class TelemetrySnapshot {
    final boolean ok;
    final String title;
    final String latency;
    final String radio;
    final String location;
    final String route;
    final String router;
    final String updated;
    final String error;

    private TelemetrySnapshot(
            boolean ok,
            String title,
            String latency,
            String radio,
            String location,
            String route,
            String router,
            String updated,
            String error) {
        this.ok = ok;
        this.title = title;
        this.latency = latency;
        this.radio = radio;
        this.location = location;
        this.route = route;
        this.router = router;
        this.updated = updated;
        this.error = error;
    }

    static TelemetrySnapshot loading() {
        return new TelemetrySnapshot(false, "Loading telemetry", "", "", "", "", "", "", "");
    }

    static TelemetrySnapshot error(String message) {
        return new TelemetrySnapshot(false, "Dashboard unavailable", "", "", "", "", "", "", message);
    }

    static TelemetrySnapshot ok(
            String title,
            String latency,
            String radio,
            String location,
            String route,
            String router,
            String updated) {
        return new TelemetrySnapshot(true, title, latency, radio, location, route, router, updated, "");
    }
}
