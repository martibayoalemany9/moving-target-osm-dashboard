# In-Flight Satellite Router, Gateway, Disruption Diagnosis, and Low-Aggression Fingerprinting Notes

Date: 2026-06-25  
Local timezone during notes: Europe/Madrid / CEST  
Workspace: `/Users/username/curriculum_2026/69de423717afa15e22861207`

## Summary

The connection shows multiple signs of an in-flight/cabin network with satellite or aeronautical backhaul:

- The laptop was behind private cabin LAN addressing.
- The visible Internet path changed between direct cabin routing and a tunnel interface.
- Earlier latency to `1.1.1.1` averaged about `408 ms`, which is strong evidence for satellite or high-latency aeronautical backhaul.
- The public egress observed earlier was `86.104.20.228`, geolocated to Paris and announced by `AS25369 Hydra Communications Ltd`.
- Later, ICMP and traceroute stopped returning useful replies, consistent with gateway filtering, captive-network protection, tunnel policy, or temporary disruption.
- The cabin DHCP network changed from `172.19.x.x / router 172.19.0.1` to `192.168.14.117 / router 192.168.12.1`, which is the strongest clue that the disruption involved a cabin network reset, reassociation, tunnel handoff, or captive gateway state change.

The exact satellite constellation or aircraft connectivity vendor is not proven from these observations. The visible public ASN identifies the Internet egress provider/POP, not necessarily the satellite operator.

## Timeline and Evidence

### Initial live route and public egress

The first successful network inspection showed the default Internet route using a tunnel interface:

```text
route to: default
gateway: 10.5.0.2
interface: utun14
mtu: 1420
```

The public IP metadata at that time was:

```json
{
  "ip": "86.104.20.228",
  "city": "Paris",
  "region": "Île-de-France",
  "country": "FR",
  "loc": "48.8534,2.3488",
  "org": "AS25369 Hydra Communications Ltd",
  "timezone": "Europe/Paris"
}
```

WHOIS confirmed:

```text
inetnum: 86.104.20.0 - 86.104.21.255
netname: FR-86-104-20
org-name: Hydra Communications Ltd
route: 86.104.20.0/23
origin: AS25369
```

Interpretation:

- `utun14` means the active default route was through a macOS tunnel interface, commonly used by VPNs, private relay systems, and similar tunneling software.
- `AS25369 Hydra Communications Ltd` is the visible public Internet egress.
- Because of the tunnel, that public IP may not be the aircraft ISP or satellite provider.

### Initial latency and traceroute evidence

Ping to Cloudflare DNS:

```text
PING 1.1.1.1 (1.1.1.1): 56 data bytes
64 bytes from 1.1.1.1: icmp_seq=0 ttl=59 time=254.569 ms
64 bytes from 1.1.1.1: icmp_seq=1 ttl=59 time=389.484 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=59 time=401.409 ms
64 bytes from 1.1.1.1: icmp_seq=3 ttl=59 time=498.632 ms
64 bytes from 1.1.1.1: icmp_seq=4 ttl=59 time=519.470 ms
64 bytes from 1.1.1.1: icmp_seq=5 ttl=59 time=384.053 ms

6 packets transmitted, 6 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 254.569/407.936/519.470/86.738 ms
```

Traceroute sample:

```text
1  10.5.0.1  453.530 ms  259.734 ms  197.917 ms
2  84.247.0.1  303.189 ms  175.105 ms  204.529 ms
3  ae15.10.rt0-par5.fr.as25369.net (5.226.136.16)  195.860 ms  271.488 ms  313.446 ms
4  cloudflare-2.par.franceix.net (37.49.238.59)  205.797 ms
   cloudflare.par.franceix.net (37.49.237.49)  110.067 ms
   cloudflare-2.par.franceix.net (37.49.238.59)  100.437 ms
5  141.101.67.125  95.469 ms
6  one.one.one.one (1.1.1.1)  99.735 ms  116.797 ms  88.107 ms
```

Interpretation:

- The average RTT of `407.936 ms` is strong evidence of satellite or high-latency aeronautical backhaul.
- The later hops show Paris peering through AS25369 and Cloudflare.
- The first hop was inside the tunnel path, not the cabin Wi-Fi router.

### Cabin Wi-Fi details before disruption

The Wi-Fi interface showed:

```text
interface: en0
inet: 172.19.5.230
DHCP router: 172.19.0.1
```

Interpretation:

- `172.19.5.230` and `172.19.0.1` are private RFC1918 addresses.
- This is consistent with an onboard/cabin NAT gateway.

### Failed or protected probe

The script `satellite_probe.sh` was run against `1.1.1.1`.

At that moment, the default route had changed to the cabin interface:

```text
route to: default
gateway: 172.19.0.1
interface: en0
mtu: 1500
Wi-Fi address: 172.19.5.230
Wi-Fi DHCP router: 172.19.0.1
```

Public egress lookup failed:

```text
Public IP: unknown
Visible org/ASN: unknown
```

ICMP failed:

```text
8 packets transmitted, 0 packets received, 100.0% packet loss
```

Traceroute failed:

```text
traceroute to 1.1.1.1, 10 hops max
1  * * *
2  * * *
3  * * *
4  * * *
5  * * *
6  * * *
7  * * *
8  * * *
9  * * *
10 * * *
```

Interpretation:

- The cabin router or upstream path was not answering ICMP or traceroute.
- This can happen because of protection against fingerprinting, captive-portal policy, ICMP filtering, tunnel policy, or temporary satellite link impairment.
- The failure did not prove total Internet outage by itself; it proved that ICMP/traceroute became unusable.

### State after the disruption

Later route and DHCP state changed again:

```text
default route:
gateway: 10.5.0.2
interface: utun14
mtu: 1420
```

Wi-Fi DHCP changed to:

```text
Wi-Fi address: 192.168.14.117
Wi-Fi DHCP router: 192.168.12.1
```

DNS state:

```text
resolver on utun14:
nameserver: 100.64.0.2

scoped resolver on en0:
nameserver: 217.110.102.2
nameserver: 217.110.102.1
```

Gentle HTTPS checks succeeded:

```text
https://www.apple.com/library/test/success.html
code=200 remote=23.210.17.55 dns=0.023026 connect=0.043897 tls=0.074498 firstbyte=0.098956 total=0.099328

https://cloudflare.com/cdn-cgi/trace
code=200 remote=104.16.132.229 dns=0.025714 connect=0.045507 tls=0.074418 firstbyte=0.175952 total=0.176394
```

Interpretation:

- The cabin LAN changed from the `172.19.x.x` network to the `192.168.x.x` network.
- The default route returned to `utun14`, meaning a tunnel again became the active Internet route.
- DNS was reachable through both the tunnel and the Wi-Fi scoped resolver.
- HTTPS worked after the disruption.

## Most Likely Cause of the Internet Disruption

The strongest evidence points to a network state change rather than a normal remote-site outage:

1. Before failure, the cabin LAN was `172.19.5.230` with router `172.19.0.1`.
2. During the failed probe, default routing went directly through `en0` and `172.19.0.1`.
3. After recovery, Wi-Fi DHCP had changed to `192.168.14.117` with router `192.168.12.1`.
4. The active Internet route returned to `utun14`.

Probable explanation:

The aircraft/cabin network or captive gateway likely reset, reassigned the client to a different private subnet, or forced a reassociation. During that transition, ICMP and traceroute were blocked or dropped, and public egress lookup failed. Once routing stabilized, the tunnel resumed and normal HTTPS worked again.

Possible contributing factors:

- The router may rate-limit or block ICMP/traceroute because those look like fingerprinting.
- The cabin network may rotate subnets or gateways when the onboard connectivity manager changes link state.
- The tunnel/VPN/private relay may have temporarily lost its underlay route when the cabin network changed.
- Satellite backhaul can have intermittent outages during beam handoff, congestion, aircraft banking, weather fade, or ground gateway handoff.

What is not proven:

- It is not proven that the diagnostic script caused the disruption.
- It is not proven that the satellite constellation is GEO, MEO, or LEO.
- It is not proven that Hydra Communications is the aircraft connectivity provider; it may only be the visible tunnel or egress provider.

## Satellite Classification

Evidence for satellite/aeronautical backhaul:

- Private cabin NAT.
- High latency sample with average `407.936 ms`.
- In-flight context.
- Public egress through a distant POP/provider rather than a local terrestrial access network.

Classification:

- `407 ms` average RTT is too high for ordinary terrestrial broadband.
- It is compatible with GEO satellite under favorable or optimized conditions, GEO under variable queueing, MEO/LEO with tunneling/queueing, or another aeronautical backhaul path.
- A classic GEO path often shows `500+ ms`, but measured RTT can vary because of proxying, acceleration, tunnel endpoints, and ICMP handling.

Best cautious statement:

The connection is strongly consistent with in-flight satellite or aeronautical backhaul. The visible gateway observed was `AS25369 Hydra Communications Ltd` in Paris, but the exact satellite network or aircraft Wi-Fi vendor was not identified.

## Low-Aggression Fingerprinting Approach

Avoid:

- Long or repeated traceroutes.
- Aggressive port scans.
- Fast repeated ICMP probes.
- Scanning private gateway ports.
- Brute-force DNS or host discovery.

Prefer:

- Single route check.
- Single DHCP/router check.
- One public IP metadata request.
- One or two HTTPS timing checks to normal web endpoints.
- Optional low-count ping only if the network tolerates it.
- Wait several seconds between probes.

Suggested gentle commands:

```bash
route -n get default
ipconfig getifaddr en0
ipconfig getoption en0 router
scutil --dns
curl -fsS --max-time 8 -o /dev/null -w 'code=%{http_code} remote=%{remote_ip} dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} firstbyte=%{time_starttransfer} total=%{time_total}\n' https://www.apple.com/library/test/success.html
curl -fsS --max-time 8 https://ipinfo.io/json
```

Use `ping` and `traceroute` only sparingly:

```bash
ping -c 3 -W 2000 1.1.1.1
traceroute -m 5 -w 2 1.1.1.1
```

## Files Created

- `satellite_probe.sh`: initial diagnostic script.
- `satellite_probe_20260625T064228Z.txt`: captured failed/protected probe report.
- `in_flight_satellite_router_gateway_disruption_diagnosis_and_low_aggression_fingerprinting_notes_2026_06_25.md`: this document.
