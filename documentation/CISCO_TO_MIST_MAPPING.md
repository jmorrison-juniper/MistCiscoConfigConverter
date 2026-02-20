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

## Cisco Password Encoding Types

Cisco uses several password encoding/hashing methods. Only some are reversible:

### Decodable (Reversible)

| Type | Format | Algorithm | Notes |
| ---- | ------ | --------- | ----- |
| **Type 0** | `password 0 <plaintext>` | None (plaintext) | No decoding needed |
| **Type 7** | `password 7 <hex>` | XOR with known key | Weak encoding, fully reversible |

**Type 7 Details:**
- Uses Vigenere cipher with fixed key: `dsfd;kfoA,.iyewrkldJKDHSUBsgvca69834ncxv9873254k;fg87`
- First 2 hex digits are seed (00-15)
- Remaining hex pairs are XORed with key starting at seed offset
- Used for: TACACS keys, line passwords, some SNMP communities

### Non-Decodable (One-Way Hashes)

| Type | Format | Algorithm | Notes |
| ---- | ------ | --------- | ----- |
| **Type 4** | `secret 4 <hash>` | SHA-256 (unsalted) | Deprecated, weak but irreversible |
| **Type 5** | `secret 5 $1$<salt>$<hash>` | MD5 crypt | Legacy, vulnerable to brute force |
| **Type 8** | `secret 8 $8$<salt>$<hash>` | PBKDF2-SHA256 | Secure, recommended |
| **Type 9** | `secret 9 $9$<salt>$<hash>` | scrypt | Most secure, recommended |

**Migration Strategy for Non-Decodable Passwords:**
- Cannot recover original password from Type 4/5/8/9
- Parser extracts username and privilege level
- User must provide new passwords during migration
- Or configure manually in Mist portal after migration

### Cisco Username Command Formats

```
username <name> privilege <level> password <type> <value>
username <name> privilege <level> secret <type> <value>
username <name> password <type> <value>
username <name> secret <type> <value>
```

**Privilege Level to Mist Role Mapping:**

| Cisco Privilege | Mist Role | Access Level |
| --------------- | --------- | ------------ |
| 15 | admin | Full administrative access |
| 5-14 | helpdesk | Limited administrative access |
| 1-4 | read | Read-only access |
| 0 | none | No access |

### Mist Local Account Configuration

Mist stores local accounts in `switch_mgmt.local_accounts`:

```json
{
  "switch_mgmt": {
    "local_accounts": {
      "admin": {
        "password": "SecurePassword123",
        "role": "admin"
      },
      "readonly": {
        "password": "ReadOnlyPass",
        "role": "read"
      }
    },
    "root_password": "RootPassword123"
  }
}
```

## Mist Template Types

Understanding the different template/profile types in Mist and when to use each:

| Template Type | API Endpoint | Purpose | Attached To |
| ------------- | ------------ | ------- | ----------- |
| Site Template | `/orgs/{org}/sitetemplates` | AP configuration (RF, WLAN) | Site (`sitetemplate_id`) |
| Gateway Template | `/orgs/{org}/gatewaytemplates` | Gateway (router/firewall) config | Site (`gatewaytemplate_id`) |
| Device Profile | `/orgs/{org}/deviceprofiles?type=gateway` | Hub gateway device config | Device (not site) |
| Network Template | `/orgs/{org}/networktemplates` | Switch/network config | Site (`networktemplate_id`) |

### Gateway Template Types

Gateway templates have a `type` field that determines their role:

| Type | Use Case | Description |
| ---- | -------- | ----------- |
| `spoke` | Branch sites | Standard branch gateway, typically connects to hub |
| `standalone` | Standalone sites | Independent gateway, no hub dependency |

**Example**: Branch sites use a Gateway Template with `type: "spoke"` named "Branch-Default-Template"

### Device Profiles for Hub Gateways

Hub gateways do NOT use Gateway Templates. Instead, they use **Device Profiles** with `type: "gateway"`:

- API: `GET/POST /api/v1/orgs/{org_id}/deviceprofiles?type=gateway`
- Naming convention: `{device_name}-Hub-Template`
- Attached at device level, not site level
- Contains hub-specific configuration (BGP hub settings, tunnel concentrator, etc.)

### Important Distinction

| Gateway Role | Template/Profile Type | Site Attachment |
| ------------ | --------------------- | --------------- |
| Branch | Gateway Template (`type: "spoke"`) | YES - `gatewaytemplate_id` on site |
| Hub | Device Profile (`type: "gateway"`) | NO - device-level profile |
| Standalone | Gateway Template (`type: "standalone"`) | YES - `gatewaytemplate_id` on site |

**Do NOT use Site Templates for gateways** - Site Templates are exclusively for AP configuration.

### Hub vs Spoke WAN Configuration Differences

WAN interface configuration differs between hub and spoke gateways:

| Aspect | Hub (Device Profile) | Spoke (Gateway Template) |
| ------ | -------------------- | ------------------------ |
| **IP Configuration** | Static recommended (VPN endpoint stability) | DHCP/PPPoE/Static all supported |
| **Variable Usage** | Supported but typically uses literal values | Variable references (e.g., `{{wan1_ip}}`) |
| **NAT Traversal** | Use `wan_ext_ip` for public IP | Not applicable |
| **VPN Path Role** | `role: "hub"` in `vpn_paths` | `role: "spoke"` in `vpn_paths` |
| **Path Selection** | Defined at org VPN level | Uses hub-defined strategy |

#### Hub-Specific WAN Fields

| Field | Location | Purpose |
| ----- | -------- | ------- |
| `wan_ext_ip` | `port_config.{port}` | Public IP for NAT scenarios (spokes reach hub via this IP) |
| `path_selection` | Org VPN object | Strategy for spoke path selection (`disabled`, `simple`, `manual`) |
| `pod` | VPN path | Pod assignment (1-128) for large deployments |

#### VPN Path Configuration

WAN interfaces participating in VPN overlay require a **role** assignment:

- `role: "hub"` - Hub gateway WAN interfaces
- `role: "spoke"` - Spoke gateway WAN interfaces
- `role: "mesh"` - Mesh VPN topology

**Note**: While hubs can technically use DHCP for WAN IP, static IP is strongly recommended because spokes need a predictable endpoint for tunnel establishment.

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

### Mist UI vs API Availability

Some settings are available via API but not exposed in the Mist web UI:

#### Site Configuration Page (Site > Configuration)

**Visible in UI:**

| Section | Settings Available |
| ------- | ------------------ |
| Information | Site name, Country, Timezone, Notes |
| Site Proxy | Proxy URL |
| Site Groups | Group membership |
| Location | Street address, Lat/Long |
| **Site Variables** | `vars` dictionary (Add Variable, Import Variables) |
| AP Firmware Upgrade | Auto-update schedule |
| Wireless Mesh | Enable mesh networking |
| RF Template | Template selection |
| Switch Management | Root password, Proxy |
| WAN Edge Management | Root password, Conductor addresses, IDP upgrade schedule |
| Mist Edge Management | FIPS, Tunnels |
| Location Services | vBLE, WiFi location, Occupancy |

**NOT Visible in UI (API Only):**

| Setting | API Location | Notes |
| ------- | ------------ | ----- |
| `remote_syslog` | `PUT /api/v1/sites/{site_id}/setting` | Must configure via API |
| `ntp_servers` | Site Settings API | May inherit from org/template |
| `dns_servers` | Site Settings API | May inherit from org/template |
| `snmp_config` | Site Settings API | May inherit from org/template |

**Key Observation**: The Site Variables section is visible in the UI and can be populated with values like:

- `branchvlan: "10"`
- `ntp1: "192.168.1.1"`
- `dns1: "1.1.1.1"`

These variables can then be referenced in Gateway Templates using `{{variable}}` syntax.

#### Organization Settings Page (Organization > Settings)

**Visible in UI:**

| Section | Settings Available |
| ------- | ------------------ |
| Organization Info | Name, ID, MSP assignment |
| Password/Session Policy | Timeouts, password requirements |
| Switch Management | Switch Proxy toggle |
| Device Management | Remote Shell Access, Role-based access |
| Firmware Upgrade | Switch/WAN Edge upgrade schedules |
| Auto-Provisioning | Site Assignment, AP Name Generation, Device Profile Assignment |
| API/Third Party Tokens | Token management |
| Marvis Minis | Custom URLs for synthetic testing |
| Integrations | Apstra, CloudShark, Juniper, Routing Assurance |
| Certificates | Mist CA, RadSec, SSL Proxy Root |
| SSO | Identity Providers, Roles |
| Session Smart Conductor | Conductor IP addresses |
| WAN Speed Test | Scheduler settings |
| Access Assurance | Mist Auth settings |
 par
**NOT Visible in Org Settings UI:**

| Setting | Notes |
| ------- | ----- |
| `ntp_servers` | Not configurable at org level in UI |
| `dns_servers` | Not configurable at org level in UI |
| `remote_syslog` | Not configurable at org level in UI |
| `snmp_config` | Not configurable at org level in UI |

**Conclusion**: NTP, DNS, Syslog, and SNMP are NOT configured at the Organization level UI. These settings flow through:

1. **Gateway Templates** - Define `ntp_servers`, `dns_servers` (with `ntpOverride`/`dnsOverride` flags)
2. **Site Settings API** - Configure `remote_syslog` (API only, no UI)
3. **Site Variables** - Store site-specific values that templates reference via `{{variable}}`

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

## Site Variables in Gateway Templates

Mist supports `{{variable}}` syntax in certain gateway template fields, allowing per-site customization without creating separate templates. Variables are resolved from:

1. **Device `vars`** - Highest priority, set on individual device
2. **Site `vars`** - Set in Site Settings, applies to all devices at site

### Fields Supporting Site Variables

Based on Mist API documentation analysis:

| Field | Supports `{{var}}` | Example | Notes |
| ----- | ------------------ | ------- | ----- |
| `extra_routes` keys | **YES** | `"{{mynetwork}}"` | Destination CIDR can be variable |
| `extra_routes6` keys | **YES** | `"{{mynetwork6}}"` | IPv6 destination can be variable |
| `port_config` keys | **YES** | `"{{myport}}"` | Port name/range can be variable |
| `port_config[].ip_config.ip` | **YES** | `"192.168.{{vlan}}.1"` | IP address |
| `port_config[].ip_config.gateway` | **YES** | `"192.168.{{vlan}}.254"` | Gateway address |
| `port_config[].ip_config.netmask` | **YES** | `"{{netmask}}"` | Netmask |
| `port_config[].description` | **YES** | `"Site: {{sitename}}"` | Interface description |
| `port_config[].vlan_id` | **YES** | `"{{branchvlan}}"` | VLAN ID |
| `ip_configs` network name | **YES** | `"BranchVlan{{branchvlan}}"` | Network name key |
| `ip_configs[].ip` | **YES** | `"192.168.{{branchvlan}}.1/24"` | Network IP/prefix |
| `ntp_servers[]` | **YES** | `"{{ntp1}}"` | NTP server address |
| `dns_servers[]` | **YES** | `"{{dns1}}"` | DNS server address |

### Fields NOT Supporting Site Variables

| Field | Type | Notes |
| ----- | ---- | ----- |
| `remote_syslog` | N/A | Not available in gateway template (Site Settings only) |
| `ntpOverride` | boolean | Flag, not string |
| `dnsOverride` | boolean | Flag, not string |

### Override Flags

Gateway templates have override flags that control inheritance from Site Settings:

| Flag | Default | Behavior |
| ---- | ------- | -------- |
| `ntpOverride` | `false` | When false, gateway inherits NTP from Site Settings |
| `dnsOverride` | `false` | When false, gateway inherits DNS from Site Settings |

**Recommendation**: Set `ntpOverride: false` and `dnsOverride: false` in gateway templates, then configure NTP/DNS at Site Settings level for centralized management.

### Example: Site Variables Configuration

**Gateway Template** (shared across all branch sites):

```yaml
port_config:
  ge-0/0/5:
    networks:
      - "BranchVlan{{branchvlan}}"
ip_configs:
  "BranchVlan{{branchvlan}}":
    ip: "192.168.{{branchvlan}}.1/24"
ntp_servers:
  - "{{ntp1}}"
  - "{{ntp2}}"
dns_servers:
  - "{{dns1}}"
  - "{{dns2}}"
```

**Site Settings** (per-site values):

```yaml
vars:
  branchvlan: "10"
  ntp1: "192.168.1.1"
  ntp2: "time.nist.gov"
  dns1: "1.1.1.1"
  dns2: "8.8.8.8"
```

**Device Config** (optional per-device override):

```yaml
vars:
  branchvlan: "20"  # Overrides site value for this device only
```

### Best Practices for Site Variables

1. **Use consistent naming**: Prefix related variables (e.g., `ntp1`, `ntp2`, `dns1`, `dns2`)
2. **Document variables**: Keep a list of expected variables in gateway template description
3. **Avoid over-templating**: Only use variables for values that actually vary between sites
4. **Test variable resolution**: Mist shows "VAR" indicator in UI for fields using variables

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

## Implementation: Site Variables Architecture

This section documents how the converter tool stores Cisco-parsed values in Mist.

### Architecture by Gateway Type

| Gateway Type | NTP/DNS Storage | Syslog Storage | Template/Profile |
| ------------ | --------------- | -------------- | ---------------- |
| **Branch** | Site Variables | Site Settings | Gateway Template (type=spoke) |
| **Standalone** | Site Variables | Site Settings | Gateway Template (type=standalone) |
| **Hub** | Device Profile | Device Profile | Device Profile (type=gateway) |

### Branch and Standalone Gateways

- **Gateway Template**: Uses variable references `{{ntp1}}`, `{{ntp2}}`, `{{dns1}}`, `{{dns2}}`
- **Site Variables**: Store actual values parsed from Cisco config
- **Site Settings**: Store syslog configuration via `remote_syslog`

**Flow**:
1. Parse Cisco config -> Extract NTP, DNS, Syslog servers
2. Create/get gateway template with variable references
3. Create/update site with `gatewaytemplate_id`
4. Call `update_site_variables()` to store NTP/DNS values
5. Call `update_site_syslog()` to configure remote_syslog

### Hub Gateways

- **Device Profile**: Stores literal NTP/DNS/Syslog values directly
- **Not site-attached**: Hub profiles are device-level, not site-level
- **No site variables**: Values go directly into profile config

**Flow**:
1. Parse Cisco config -> Extract NTP, DNS, Syslog servers
2. Create/get device profile with NTP/DNS/Syslog values
3. Assign gateway devices to profile
4. Site does NOT get `gatewaytemplate_id` (cleared if switching from branch)

### Variable Names

| Variable | Purpose | Example Value |
| -------- | ------- | ------------- |
| `ntp1` | Primary NTP server | `10.0.0.1` |
| `ntp2` | Secondary NTP server | `10.0.0.2` |
| `dns1` | Primary DNS server | `8.8.8.8` |
| `dns2` | Secondary DNS server | `1.1.1.1` |

### API Endpoints Used

| Operation | API Endpoint | Method |
| --------- | ------------ | ------ |
| Update site variables | `/api/v1/sites/{site_id}/setting` | PUT |
| Update site syslog | `/api/v1/sites/{site_id}/setting` | PUT |
| Create gateway template | `/api/v1/orgs/{org_id}/gatewaytemplates` | POST |
| Create device profile | `/api/v1/orgs/{org_id}/deviceprofiles` | POST |

### Code References

- `site_manager.py`: `update_site_variables()`, `update_site_syslog()`
- `template_manager.py`: `create()`, `get_or_create_branch_template()`, `get_or_create_standalone_template()`
- `profile_manager.py`: `create()`, `get_or_create_hub_profile()`
- `app.py`: Orchestrates the conversion flow

---

## Features Requiring Workarounds

Some Cisco features have no native Mist API equivalent but can be configured via `additional_config_cmds` (raw Junos CLI).

### NetFlow / Flow Export

**Cisco Config:**

```text
flow exporter EXPORTER-1
 destination 10.1.1.100
 transport udp 2055
 export-protocol netflow-v9
!
flow monitor MONITOR-1
 exporter EXPORTER-1
 record netflow ipv4
!
interface GigabitEthernet0/0
 ip flow monitor MONITOR-1 input
```

**Mist API Status:** No native support for flow export configuration.

**Mist Alternative:** Built-in traffic analytics via Mist cloud (no external collector needed).

**Workaround via additional_config_cmds:**

```json
{
  "additional_config_cmds": [
    "set forwarding-options sampling instance NETFLOW input rate 1000",
    "set forwarding-options sampling instance NETFLOW family inet output flow-server 10.1.1.100 port 2055",
    "set forwarding-options sampling instance NETFLOW family inet output flow-server 10.1.1.100 version 9",
    "set forwarding-options sampling instance NETFLOW family inet output inline-jflow source-address 10.2.1.10"
  ]
}
```

**Parser Status:** Cisco NetFlow config is already parsed (`flow exporter`, `flow monitor`) but not yet converted to Junos jflow commands. Future enhancement could auto-generate `additional_config_cmds`.

---

## References

- Mist OpenAPI 3.0 Specification: `documentation/mist-api-openapi3yaml.yaml`
- Mist OpenAPI 3.1 Specification: `documentation/mist-api-openapi31yaml.yaml`
