# Cisco to Mist Configuration Mapping Reference

This document tracks terminology conversions and configuration mappings between Cisco IOS/IOS-XE and Juniper Mist.

## Device Type Terminology

| Cisco Term | Mist Term | Notes |
| ------------ | ----------- | ------- |
| Router | Gateway | Mist uses "gateway" for all routing/firewall devices |
| Firewall | Gateway | Same as router in Mist terminology |
| Layer 3 Switch | Gateway or Switch | Depends on primary function |
| Access Switch | Switch | Standard switch device |
| Wireless Controller | N/A | Mist is controller-less; APs connect directly to cloud |
| Access Point | AP | Direct cloud-managed |

## Configuration Hierarchy

Understanding where settings can be applied in Mist:

### Gateway Configuration Levels

| Setting Category | Gateway Template | Device (Gateway) | Site Settings |
| ----------------- | ------------------ | ------------------ | --------------- |
| BGP Config | YES | YES | NO |
| DHCP Server | YES | YES | YES |
| DNS Servers | YES | YES | YES |
| NTP Servers | YES | YES | YES |
| Port Config | YES | YES | NO |
| Routing Policies | YES | YES | NO |
| VRF Instances | YES | YES | NO |
| **Remote Syslog** | **NO** | **NO** | **YES** |
| **SNMP Config** | **NO** | **NO** | **YES** |

**Key Finding**: Gateway logging (remote_syslog) is configured at **Site Settings level only**, not at the gateway template or individual device level.

#### mistapi Library Confirmation

Verified in mistapi 0.59.x source code:

| Module | Function | Has Syslog? |
| -------- | ---------- | ------------- |
| `mistapi.api.v1.sites.setting` | `updateSiteSettings(site_id, body)` | **YES** - body includes `remote_syslog` |
| `mistapi.api.v1.orgs.gatewaytemplates` | `createOrgGatewayTemplate(org_id, body)` | NO |
| `mistapi.api.v1.orgs.devices` | Device operations | NO syslog references |
| `mistapi.api.v1.orgs.sites` | `searchOrgSites()` | `remote_syslog_enabled` as search filter |

**API Endpoint for Gateway Logging**:

- `PUT /api/v1/sites/{site_id}/setting`
- Include `remote_syslog` object in the request body

### Switch Configuration Levels

| Setting Category | Network Template | Device (Switch) | Site Settings |
| ----------------- | ------------------ | ----------------- | --------------- |
| Port Config | YES | YES | YES |
| OSPF Config | YES | YES | NO |
| RADIUS Config | YES | YES | YES |
| **Remote Syslog** | **YES** | NO | **YES** |
| **SNMP Config** | **YES** | NO | **YES** |

## Logging Configuration

### Cisco Logging to Mist Remote Syslog

| Cisco Command | Mist Configuration | Location |
| --------------- | ------------------- | ---------- |
| `logging host <ip>` | `remote_syslog.servers[].host` | Site Settings |
| `logging trap <level>` | `remote_syslog.servers[].severity` | Site Settings |
| `logging facility <fac>` | `remote_syslog.servers[].facility` | Site Settings |
| `logging source-interface <int>` | `remote_syslog.servers[].source_address` | Site Settings |
| `logging buffered <size>` | `remote_syslog.archive.size` | Site Settings |
| N/A | `remote_syslog.servers[].protocol` | UDP or TCP (default: UDP) |
| N/A | `remote_syslog.servers[].port` | Default: 514 |

### Mist Remote Syslog Schema

```yaml
remote_syslog:
  enabled: true
  send_to_all_servers: false
  network: "default"  # VRF/network for source address
  time_format: "millisecond"  # millisecond | year | year millisecond
  servers:
    - host: "syslog.example.com"
      port: 514
      protocol: "udp"  # udp | tcp
      facility: "any"  # any|authorization|config|daemon|firewall|kernel|ntp|security|user
      severity: "info"  # alert|any|critical|emergency|error|info|notice|warning
      tag: ""
      routing_instance: ""
      source_address: ""
  archive:
    files: 20
    size: "5m"
  console:
    contents:
      - facility: "config"
        severity: "warning"
```

### Severity Level Mapping

| Cisco Level | Cisco Name | Mist Severity |
| ------------- | ------------ | --------------- |
| 0 | emergencies | emergency |
| 1 | alerts | alert |
| 2 | critical | critical |
| 3 | errors | error |
| 4 | warnings | warning |
| 5 | notifications | notice |
| 6 | informational | info |
| 7 | debugging | any |

### Facility Mapping

| Cisco Facility | Mist Facility |
| ---------------- | --------------- |
| local0-local7 | user |
| auth | authorization |
| syslog | any |
| kern | kernel |
| daemon | daemon |
| ftp | ftp |
| ntp | ntp |
| security | security |

## Gateway Management (Host-Out Policies)

For controlling how gateway management traffic (NTP, TACACS, RADIUS, SYSLOG, SNMP) egresses:

```yaml
gateway_mgmt:
  host_out_policies:
    syslog:
      path_preference: "broadband_wans"
      servers:
        - host: "103.35.3.5"
          server_name: "dc_syslog_server"
          path_preference: "dc_only"
    dns:
      path_preference: "wan_paths"
    ntp:
      path_preference: "wan_paths"
```

## Interface Configuration

### Cisco Interface to Mist Port Config

| Cisco Command | Mist Configuration | Location |
| --------------- | ------------------- | ---------- |
| `interface GigabitEthernet0/0` | `port_config["ge-0/0/0"]` | Gateway Template |
| `ip address <ip> <mask>` | `ip_configs[network].ip` | Gateway Template |
| `description <text>` | `port_config[].description` | Gateway Template |
| `shutdown` | `port_config[].disabled: true` | Gateway Template |

### Interface Naming Convention

| Cisco Interface | Mist/Junos Interface |
| ----------------- | --------------------- |
| GigabitEthernet0/0 | ge-0/0/0 |
| GigabitEthernet0/0/0 | ge-0/0/0 |
| TenGigabitEthernet0/0 | xe-0/0/0 |
| Port-channel1 | ae0 |
| Loopback0 | lo0 |
| Vlan100 | irb.100 |
| Tunnel0 | st0 (or tunnel config) |

## Routing Configuration

### Static Routes

| Cisco Command | Mist Configuration |
| --------------- | ------------------- |
| `ip route <dest> <mask> <next-hop>` | `extra_routes["<dest>/<prefix>"].via` |
| `ip route vrf <vrf> <dest> <mask> <next-hop>` | `vrf_instances[vrf].extra_routes` |

### BGP Configuration

| Cisco Command | Mist Configuration |
| --------------- | ------------------- |
| `router bgp <asn>` | `bgp_config[name].local_as` |
| `neighbor <ip> remote-as <asn>` | `bgp_config[name].neighbors[ip].remote_as` |
| `network <prefix>` | `bgp_config[name].networks[]` |

## NTP Configuration

| Cisco Command | Mist Configuration | Location |
| --------------- | ------------------- | ---------- |
| `ntp server <ip>` | `ntp_servers[]` | Site Settings or Gateway Template |
| `ntp source <interface>` | Via network/routing config | N/A |

## DNS Configuration

| Cisco Command | Mist Configuration | Location |
| --------------- | ------------------- | ---------- |
| `ip name-server <ip>` | `dns_servers[]` | Site Settings or Gateway Template |
| `ip domain name <name>` | `dns_suffix[]` | Site Settings or Gateway Template |

## AAA/TACACS Configuration

| Cisco Command | Mist Configuration | Location |
| --------------- | ------------------- | ---------- |
| `tacacs server <name>` | `switch_mgmt.tacacs.tacplus_servers[]` | Site Settings |
| `tacacs-server host <ip>` | `tacplus_servers[].host` | Site Settings |
| `tacacs-server key <key>` | `tacplus_servers[].secret` | Site Settings |

## SNMP Configuration

| Cisco Command | Mist Configuration | Location |
| --------------- | ------------------- | ---------- |
| `snmp-server community <string>` | `snmp_config.community` | Site Settings |
| `snmp-server location <loc>` | `snmp_config.location` | Site Settings |
| `snmp-server contact <contact>` | `snmp_config.contact` | Site Settings |
| `snmp-server host <ip>` | `snmp_config.trap_groups[].targets[]` | Site Settings |

## API Endpoints Reference

| Resource | Mist API Endpoint |
| ---------- | ------------------ |
| Gateway Templates | `GET/POST /api/v1/orgs/{org_id}/gatewaytemplates` |
| Site Settings | `GET/PUT /api/v1/sites/{site_id}/setting` |
| Org Settings | `GET/PUT /api/v1/orgs/{org_id}/setting` |
| Device Gateway | `GET/PUT /api/v1/sites/{site_id}/devices/{device_id}` |

## Mist Configuration Hierarchy

1. **Org Settings** - Global defaults for the organization
2. **Gateway Template** - Applied to sites, defines gateway configuration
3. **Site Settings** - Site-specific overrides
4. **Device Config** - Individual device overrides

Lower levels override higher levels for conflicting settings.

---

## Conversion Workflow

### User Input Requirements

Cisco configs do NOT contain:

- Site name or location
- Whether the device is a Hub or Branch gateway

The user must select the gateway type (Branch or Hub) via the UI before conversion.

### Conversion Process

1. **User selects config file** from INPUT folder or uploads new file
2. **User selects Gateway Type** (Branch or Hub radio button)
3. **User clicks Convert** button
4. **Extract device hostname** from Cisco config (`hostname <name>`)
5. **Check if Site exists** in Mist org by that name
   - Use `GET /api/v1/orgs/{org_id}/sites` or `searchOrgSites()`
6. **Site resolution**:
   - If site EXISTS: use the existing site_id
   - If site does NOT exist: create new site with:
     - `name`: device hostname
     - `address`: physical location from config (if available via SNMP location)
7. **Show confirmation dialog** with proposed changes before API call
8. **User confirms** -> Send to Mist API
9. **Apply configuration** to Site Settings (logging, SNMP, etc.)

### Confirmation Dialog Content

Before sending to Mist API, display:

```text
Proposed Changes:
-----------------
Gateway Type: [Branch/Hub]
Device Name: [hostname from config]
Site: [Existing: site_name] or [NEW: site_name]

Configuration to Apply:
- Remote Syslog: [server count] servers
- NTP Servers: [count]
- DNS Servers: [count]
- Static Routes: [count]
- Interfaces: [count]

[Cancel] [Confirm & Apply]
```

### Data Extraction from Cisco Config

| Config Element | Cisco Command | Use in Mist |
| ---------------- | --------------- | ------------- |
| Device Name | `hostname <name>` | Site name (if new site needed) |
| Physical Location | `snmp-server location <loc>` | Site address field |
| Contact Info | `snmp-server contact <contact>` | Site notes or SNMP contact |

### API Calls Sequence

1. `GET /api/v1/orgs/{org_id}/sites` - List existing sites
2. `POST /api/v1/orgs/{org_id}/sites` - Create site (if needed)
3. `PUT /api/v1/sites/{site_id}/setting` - Apply site settings (logging, SNMP, NTP, DNS)
4. Gateway template association handled separately based on Hub/Branch selection

---

## References

- Mist OpenAPI 3.0 Specification: `documentation/mist-api-openapi3yaml.yaml`
- Mist OpenAPI 3.1 Specification: `documentation/mist-api-openapi31yaml.yaml`
