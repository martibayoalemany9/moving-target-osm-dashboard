package com.movingtarget.auto;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

final class TelemetryRepository {
    private TelemetryRepository() {
    }

    static TelemetrySnapshot fetchLatest(String samplesUrl) {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(samplesUrl);
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(2500);
            connection.setReadTimeout(2500);
            connection.setRequestProperty("Accept", "application/json");

            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                return TelemetrySnapshot.error("HTTP " + status + " from " + samplesUrl);
            }

            StringBuilder body = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    body.append(line);
                }
            }

            JSONObject payload = new JSONObject(body.toString());
            JSONArray samples = payload.optJSONArray("samples");
            if (samples == null || samples.length() == 0) {
                return TelemetrySnapshot.error("No samples yet. Start the dashboard collector.");
            }

            JSONObject sample = samples.getJSONObject(samples.length() - 1);
            return toSnapshot(sample);
        } catch (Exception e) {
            return TelemetrySnapshot.error(e.getClass().getSimpleName() + ": " + e.getMessage());
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static TelemetrySnapshot toSnapshot(JSONObject sample) {
        JSONObject latency = sample.optJSONObject("latency");
        JSONObject location = sample.optJSONObject("location");
        JSONObject radio = sample.optJSONObject("radio");
        JSONObject routerGeo = sample.optJSONObject("router_geo");
        JSONObject selectedHop = routerGeo == null ? null : routerGeo.optJSONObject("selected_public_hop");
        JSONObject geoip = routerGeo == null ? null : routerGeo.optJSONObject("geoip");

        String sequence = sample.optString("sequence", "?");
        String title = "Sample " + sequence;
        String latencyText = "Latency: " + formatMs(latency == null ? Double.NaN : latency.optDouble("avg_ms", Double.NaN));
        String radioText = compactRadio(radio);
        String locationText = compactLocation(location);
        String routeText = compactRoute(sample.optJSONObject("route_correction"));
        String routerText = compactRouter(selectedHop, geoip);
        String updated = sample.optString("timestamp_utc", "");

        return TelemetrySnapshot.ok(title, latencyText, radioText, locationText, routeText, routerText, updated);
    }

    private static String formatMs(double value) {
        if (Double.isNaN(value)) {
            return "waiting";
        }
        return String.format(Locale.US, "%.0f ms", value);
    }

    private static String compactRadio(JSONObject radio) {
        if (radio == null) {
            return "Radio: unavailable";
        }
        String network = firstNonEmpty(radio.optString("network_type"), radio.optString("data_network_type"), radio.optString("rat"));
        String operator = firstNonEmpty(radio.optString("operator"), radio.optString("carrier"), radio.optString("plmn"));
        if (network.isEmpty() && operator.isEmpty()) {
            return "Radio: captured";
        }
        return "Radio: " + joinNonEmpty(" / ", network, operator);
    }

    private static String compactLocation(JSONObject location) {
        if (location == null) {
            return "Location: waiting";
        }
        double lat = location.optDouble("lat", Double.NaN);
        double lon = location.optDouble("lon", Double.NaN);
        if (Double.isNaN(lat) || Double.isNaN(lon)) {
            return "Location: waiting";
        }
        return String.format(Locale.US, "Location: %.5f, %.5f", lat, lon);
    }

    private static String compactRoute(JSONObject correction) {
        if (correction == null) {
            return "Route: uncorrected";
        }
        String route = firstNonEmpty(correction.optString("route"), correction.optString("track"), correction.optString("track_id"));
        double distance = correction.optDouble("distance_km", Double.NaN);
        if (route.isEmpty() && Double.isNaN(distance)) {
            return "Route: uncorrected";
        }
        if (Double.isNaN(distance)) {
            return "Route: " + route;
        }
        return String.format(Locale.US, "Route: %s, %.1f km off", route.isEmpty() ? "selected" : route, distance);
    }

    private static String compactRouter(JSONObject selectedHop, JSONObject geoip) {
        String ip = selectedHop == null ? "" : selectedHop.optString("ip");
        String city = geoip == null ? "" : geoip.optString("city");
        String org = geoip == null ? "" : geoip.optString("org");
        String details = joinNonEmpty(" / ", ip, city, org);
        return details.isEmpty() ? "Router: waiting" : "Router: " + details;
    }

    private static String firstNonEmpty(String... values) {
        for (String value : values) {
            if (value != null && !value.trim().isEmpty() && !"null".equals(value)) {
                return value.trim();
            }
        }
        return "";
    }

    private static String joinNonEmpty(String separator, String... values) {
        StringBuilder builder = new StringBuilder();
        for (String value : values) {
            if (value == null || value.trim().isEmpty() || "null".equals(value)) {
                continue;
            }
            if (builder.length() > 0) {
                builder.append(separator);
            }
            builder.append(value.trim());
        }
        return builder.toString();
    }
}
