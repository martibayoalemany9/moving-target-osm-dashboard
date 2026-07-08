#!/usr/bin/env bash
set -u

TARGET="${1:-1.1.1.1}"
PING_COUNT="${PING_COUNT:-8}"
PING_TIMEOUT="${PING_TIMEOUT:-2}"
TRACE_MAX_HOPS="${TRACE_MAX_HOPS:-10}"
OUT="satellite_probe_$(date -u +%Y%m%dT%H%M%SZ).txt"

have() { command -v "$1" >/dev/null 2>&1; }

section() {
  printf '\n## %s\n' "$1" | tee -a "$OUT"
}

run_capture() {
  printf '\n$ %s\n' "$*" >>"$OUT"
  "$@" >>"$OUT" 2>&1
}

first_line() {
  awk 'NF { print; exit }'
}

default_route() {
  if have route; then
    route -n get default 2>/dev/null
  elif have ip; then
    ip route get "$TARGET" 2>/dev/null
  fi
}

public_ip_json() {
  if have curl; then
    curl -fsS --max-time 8 https://ipinfo.io/json 2>/dev/null \
      || curl -fsS --max-time 8 https://ifconfig.co/json 2>/dev/null \
      || true
  fi
}

json_field() {
  # Good enough for ipinfo/ifconfig flat JSON without requiring jq.
  sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

wifi_device() {
  if have networksetup; then
    networksetup -listallhardwareports 2>/dev/null \
      | awk '/Hardware Port: (Wi-Fi|AirPort)/ { getline; if ($1 == "Device:") print $2 }' \
      | head -n 1
  fi
}

iface_addr() {
  local iface="$1"
  if have ipconfig; then
    ipconfig getifaddr "$iface" 2>/dev/null || true
  elif have ip; then
    ip -4 addr show "$iface" 2>/dev/null | awk '/inet / { sub(/\/.*/, "", $2); print $2; exit }'
  fi
}

iface_router() {
  local iface="$1"
  if have ipconfig; then
    ipconfig getoption "$iface" router 2>/dev/null || true
  fi
}

ping_sample() {
  if ping -h 2>&1 | grep -q -- '-W'; then
    ping -c "$PING_COUNT" -W "$PING_TIMEOUT" "$TARGET"
  else
    ping -c "$PING_COUNT" "$TARGET"
  fi
}

avg_rtt_ms() {
  awk -F'[=/ ]+' '
    /round-trip|rtt/ {
      if ($(NF - 4) ~ /^[0-9.]+$/) {
        print $(NF - 3)
      } else if ($(NF - 3) ~ /^[0-9.]+$/) {
        print $(NF - 2)
      }
      exit
    }'
}

classify_rtt() {
  local avg="$1"
  awk -v avg="$avg" 'BEGIN {
    if (avg == "" || avg <= 0) {
      print "unknown: no usable ping RTT sample"
    } else if (avg >= 500) {
      print "very strong GEO-satellite evidence: average RTT is >= 500 ms"
    } else if (avg >= 250) {
      print "strong satellite/backhaul evidence: average RTT is 250-500 ms; this fits GEO under load or a high-latency aeronautical path"
    } else if (avg >= 120) {
      print "moderate satellite/backhaul evidence: average RTT is 120-250 ms; this fits some MEO/LEO or tunneled aeronautical paths"
    } else if (avg >= 40) {
      print "not enough latency proof by itself: average RTT is 40-120 ms; this could be LEO satellite, air-to-ground, or normal terrestrial Internet"
    } else {
      print "low satellite likelihood from latency alone: average RTT is < 40 ms"
    }
  }'
}

rm -f "$OUT"
{
  echo "Satellite connectivity probe"
  echo "UTC time: $(date -u '+%Y-%m-%d %H:%M:%S')"
  echo "Target: $TARGET"
} | tee "$OUT" >/dev/null

section "Local gateway"
route_text="$(default_route || true)"
printf '%s\n' "$route_text" >>"$OUT"
route_iface="$(printf '%s\n' "$route_text" | awk '/interface:/ { print $2; exit }')"
route_gw="$(printf '%s\n' "$route_text" | awk '/gateway:/ { print $2; exit }')"

wifi="$(wifi_device)"
[ -n "$wifi" ] || wifi="en0"
wifi_ip="$(iface_addr "$wifi" | first_line)"
wifi_router="$(iface_router "$wifi" | first_line)"
printf 'Detected Wi-Fi candidate: %s\n' "$wifi" | tee -a "$OUT"
printf 'Wi-Fi address: %s\n' "${wifi_ip:-unknown}" | tee -a "$OUT"
printf 'Wi-Fi DHCP router: %s\n' "${wifi_router:-unknown}" | tee -a "$OUT"
printf 'Default route interface: %s\n' "${route_iface:-unknown}" | tee -a "$OUT"
printf 'Default route gateway: %s\n' "${route_gw:-unknown}" | tee -a "$OUT"

section "Public egress"
ip_json="$(public_ip_json)"
printf '%s\n' "$ip_json" >>"$OUT"
pub_ip="$(printf '%s\n' "$ip_json" | json_field ip)"
org="$(printf '%s\n' "$ip_json" | json_field org)"
city="$(printf '%s\n' "$ip_json" | json_field city)"
region="$(printf '%s\n' "$ip_json" | json_field region)"
country="$(printf '%s\n' "$ip_json" | json_field country)"
printf 'Public IP: %s\n' "${pub_ip:-unknown}" | tee -a "$OUT"
printf 'Visible org/ASN: %s\n' "${org:-unknown}" | tee -a "$OUT"
printf 'Geo hint: %s %s %s\n' "${city:-}" "${region:-}" "${country:-}" | tee -a "$OUT"

if [ -n "${pub_ip:-}" ] && have dig; then
  ptr="$(dig +short -x "$pub_ip" 2>/dev/null | first_line)"
  printf 'Reverse DNS: %s\n' "${ptr:-none}" | tee -a "$OUT"
fi

section "Latency sample"
ping_output="$(ping_sample 2>&1 || true)"
printf '%s\n' "$ping_output" | tee -a "$OUT"
avg="$(printf '%s\n' "$ping_output" | avg_rtt_ms)"
verdict="$(classify_rtt "$avg")"

section "Traceroute sample"
if have traceroute; then
  run_capture traceroute -m "$TRACE_MAX_HOPS" -w 2 "$TARGET"
elif have tracepath; then
  run_capture tracepath "$TARGET"
else
  echo "No traceroute or tracepath command available." | tee -a "$OUT"
fi

section "Verdict"
printf 'RTT verdict: %s\n' "$verdict" | tee -a "$OUT"

if printf '%s\n' "$route_iface" | grep -q '^utun'; then
  echo "Tunnel note: default Internet route uses a utun interface, so a VPN/iCloud relay/private tunnel is probably hiding the aircraft provider from public-IP checks." | tee -a "$OUT"
fi

if printf '%s\n%s\n' "$wifi_ip" "$wifi_router" | grep -Eq '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)'; then
  echo "Cabin LAN note: Wi-Fi is on private RFC1918 space, consistent with an onboard router/NAT gateway." | tee -a "$OUT"
fi

if [ -n "${org:-}" ]; then
  printf 'Visible gateway provider: %s\n' "$org" | tee -a "$OUT"
fi

cat <<'EOF' | tee -a "$OUT"
Interpretation:
- High RTT plus private cabin NAT is the practical proof of a satellite or long-haul aeronautical backhaul.
- Public IP/ASN names the visible Internet egress POP. It may be a VPN, airline provider, or data-center exit, not the satellite constellation itself.
- GEO usually shows about 500+ ms RTT. MEO/LEO and tunneled or congested paths can sit lower, so traceroute names and airline portal branding are needed for a provider-level claim.
EOF

printf '\nSaved full report: %s\n' "$OUT"
