```instructions
# AI Agent Instructions

## Project Overview
**Target Audience**: Engineers. Use clear, professional language without jargon. Think Fred Rogers meets NASA/JPL safety standards.

## Runtime Environment
- **Python**: 3.13+
- **Container Runtime**: Podman (primary), Docker (compatible)
- **WSGI Server**: Gunicorn with gevent workers
- **API SDK**: mistapi 0.59.x+

### Container Notes
- Use `--format docker` with Podman for HEALTHCHECK support
- Containerfile is compatible with both Podman and Docker
- Volume mounts use `:Z` for SELinux compatibility

## Critical Patterns

### Safety-First Coding
```python
# DESTRUCTIVE operations require explicit confirmation
confirmation = input("Type 'CONFIRM' to proceed: ")
if confirmation != "CONFIRM":
    return  # NASA/JPL: early return on validation failure
```

### Logging Standards
- **Debug**: Internal state changes, API responses
- **Info**: User-facing progress messages
- **Error**: Exception context with full traceback
- **Never log secrets**: Redact tokens/passwords
- **ASCII Only**: Replace Unicode with ASCII equivalents

### Input Validation
```python
def validate_input(value: str) -> bool:
    """All external inputs validated before use"""
    # Reject path traversal, special chars, etc.
```

### File Path Management
- **All outputs**: `data/` directory (enforced at runtime)
- Use `os.path.join()` or `Path()`, never hardcoded `/` or `\\`

## Coding Conventions

### Naming Standards
- **No abbreviations**: `for device in devices` NOT `for d in devices`
- **No AI markers**: Never use `...existing code...` or double ellipses
- **Class-based**: All features organized under semantic class names

### Error Handling
```python
# Always handle potential exceptions gracefully
try:
    result = perform_operation()
except SpecificException as error:
    logging.error(f"Operation failed: {error}")
    return None
```

### Windows Path Compatibility
Use `os.path.join()` or `Path()`, never hardcoded `/` or `\\`

## Documentation Structure
- **README.md**: User-facing guide
- **requirements.txt**: Python dependencies
- **documentation/CISCO_TO_MIST_MAPPING.md**: Cisco to Mist terminology and config mapping reference
- **documentation/mist-api-openapi3yaml.yaml**: Mist OpenAPI 3.0 specification
- `.env` (git-ignored): Credentials & config

## Mist Terminology
- **Gateway** = Router or Firewall (Mist uses "gateway" for all routing/firewall devices)
- **Switch** = Layer 2/3 switch
- **AP** = Access Point (cloud-managed, no controller needed)

Refer to `documentation/CISCO_TO_MIST_MAPPING.md` for full conversion mappings.

## Hardware Type Selection

### SRX vs SSR Toggle
- **SRX** (default): Juniper SRX Series (vSRX, SRX300, SRX1500, etc.)
- **SSR**: Session Smart Router (128T)

### Hardware-Specific Settings
- **DNS Suffix**: Only applies to SRX gateways (SSR does not support this setting)

## Hub vs Spoke Gateway Configuration

### Gateway Type Objects
| Gateway Role | Mist Object | Type Field | Site Attachment |
|-------------|-------------|------------|-----------------|
| **Hub** | Device Profile (`deviceprofile_gateway`) | `type: "gateway"` | Device-level |
| **Branch (Spoke)** | Gateway Template (`gateway_template`) | `type: "spoke"` | Site-level |
| **Standalone** | Gateway Template (`gateway_template`) | `type: "standalone"` | Site-level |

### Hub-Specific WAN Configuration
- **Static IP Recommended**: Hubs should use static IP for VPN endpoint stability (spokes need predictable endpoint)
- **DHCP Warning**: If Cisco config uses DHCP on WAN, display warning when hub type is selected
- **`wan_ext_ip`**: Hub-only field for NAT traversal (public IP for spokes to reach hub behind NAT)
- **VPN Path Role**: Set `role: "hub"` in `vpn_paths` configuration

### Variable Usage Difference
- **Hub (Device Profile)**: Typically uses literal values (IPs, etc.) directly in profile
- **Spoke (Gateway Template)**: Uses variable references like `{{wan1_ip}}` resolved via site variables

## WAN Interface Detection

### Classification Rules
- **Tunnel interfaces** (e.g., Tunnel0, Tunnel100) are **NOT** WAN interfaces - they are logical overlays
- Tunnel interfaces get `interface_role="tunnel"` with early return from classification
- WAN classification uses weighted scoring based on interface characteristics

### Cellular Interface Handling
Each cellular interface requires **TWO** configuration reservations:
1. **Modem GigE slot** - External 3rd-party cellular modem connection (wan_type="broadband")
2. **LTE slot** - Built-in Juniper LTE interface (wan_type="lte")

### Variable Naming Convention
- Sequential numbering: wan1, wan2, wan3, etc.
- LTE slots get `_lte` suffix: wan3_lte, wan4_lte
- No maximum limit on WAN interfaces

### Ordering Rules
1. GigE WAN interfaces (regular WAN, sorted by wan_score)
2. (Future: GigE LAN interfaces)
3. Cellular modem GigE slots (by interface name)
4. Cellular LTE slots (by interface name, with _lte suffix)

### Data Structures
- `wan_interface_list`: Full list of WAN interface dicts with metadata
- Each interface dict contains: `name`, `wan_var_name`, `wan_type`, `is_cellular`, `cellular_slot`, plus all parsed details

### WAN Interface Parsed Details
Each WAN interface extracts the following from Cisco config:
- **ip_address**: Static IP if configured
- **subnet_mask**: Subnet mask for static IPs
- **ip_config_type**: "static", "dhcp", "pppoe", or "negotiated"
- **encap_vlan_id**: VLAN ID for subinterface encapsulation (dot1q)
- **default_gateway**: Next-hop from static routes
- **shutdown**: True if interface is administratively down (shutdown command)
- **cellular_profile_id**: ID of cellular data profile attached to this interface
- **upload_kbps**: Upload bandwidth for traffic shaping (see below)
- **download_kbps**: Download bandwidth for traffic shaping (see below)
- **bandwidth_source**: Where bandwidth was derived from

### WAN Bandwidth / Traffic Shaping
Bandwidth for traffic shaping is extracted from multiple sources in priority order:

1. **Explicit bandwidth command**: `bandwidth <kbps>` on the interface (treated as symmetric)
2. **Tunnel bandwidth**: If a tunnel sources from this interface with a bandwidth command
3. **Tunnel description**: Speed hints in tunnel description (e.g., "100M MPLS", "BDW=500/500")
4. **Interface description**: Speed hints in the interface description

Speed patterns recognized in descriptions:
- `BDW=500/500` - Carrier format for upload/download in Mbps (asymmetric supported)
- `100M`, `100Mbps`, `100 Mbps`, `100Meg` - Symmetric speeds
- `1G`, `1Gbps`, `1 Gig`, `1Gb`
- `10G`, `10Gbps`
- Patterns like "BDIA 100Meg", "Internet 1G", "50M MPLS"

The `bandwidth_source` field indicates where the value came from:
- `config`: From explicit `bandwidth` command on interface
- `tunnel:TunnelX`: From tunnel's bandwidth command
- `tunnel_desc:TunnelX`: From tunnel's description
- `description`: From interface description

### Traffic Shaping Variable Strategy
Gateway templates **always** include traffic shaping with variable references:
```json
"traffic_shaping": {
    "enabled": true,
    "max_tx_kbps": "{{wan1_upload_kbps}}"
}
```

Site variables (`wan1_upload_kbps`, `wan1_download_kbps`) are **only created when values exist**.
If a site doesn't have the variable defined, that portion of the template doesn't "resolve" and
the traffic shaping config doesn't become active for that port.

### IP Configuration Types
- **static**: `ip address X.X.X.X Y.Y.Y.Y`
- **dhcp**: `ip address dhcp`
- **pppoe**: `pppoe-client dial-pool-number X` or `pppoe enable group X`
- **negotiated**: `ip address negotiated` (PPP/PPPoE negotiated)

### Cellular Profile Parsing
Cisco cellular/LTE configurations are parsed from:
- `profile cellular data <id>` blocks
- `controller Cellular <slot>` with `lte sim data-profile` commands

Extracted cellular profile fields:
- **profile_id**: Integer ID of the data profile
- **apn**: APN name (e.g., "internet.carrier.com")
- **authentication**: none, chap, pap, or pap_chap
- **username**: APN username
- **password**: APN password (Type 7 encoded passwords are decoded)
- **slot**: SIM slot number (0 or 1)

Example Cisco config:
```
profile cellular data 1
 apn internet.carrier.com
 authentication chap
 username apn_user password 7 070C285F4D
 
controller Cellular 0/2/0
 lte sim data-profile 1 slot 0
```

### WAN Site Variables
Each WAN interface creates multiple site variables for the Mist portal:
- `wan1` = interface name (e.g., "GigabitEthernet0/0/0")
- `wan1_ip` = IP address (e.g., "10.1.1.1")
- `wan1_subnet` = subnet mask (e.g., "255.255.255.0")
- `wan1_gateway` = default gateway (e.g., "10.1.1.254")
- `wan1_vlan` = VLAN ID if applicable (e.g., "100")
- `wan1_type` = IP config type (static, dhcp, pppoe, negotiated)

For LTE interfaces (wan_type == "lte"), additional variables are created:
- `wan3_lte_apn` = APN name (e.g., "internet.carrier.com")
- `wan3_lte_auth` = authentication type (none, chap, pap)
- `wan3_lte_user` = APN username
- `wan3_lte_pass` = APN password

These variables can be referenced in gateway templates using `{{wan1}}`, `{{wan1_ip}}`, `{{wan3_lte_apn}}`, etc.

## Power User Features

When `POWERUSER=true` is set in `.env`, additional administrative buttons appear in the web UI.

### Backup/Restore/Delete Operations
These features provide bulk management of Mist organization objects:

| Object Type | Backup | Restore | Delete All |
|-------------|--------|---------|------------|
| Service Policies | Yes | Yes | No |
| Services | Yes | Yes | Yes |
| Networks | Yes | Yes | Yes |

### API Routes
- `POST /api/poweruser/backup-service-policies` - Backup service policies to JSON
- `GET /api/poweruser/list-service-policy-backups` - List available backups
- `POST /api/poweruser/restore-service-policies` - Restore from backup (CONFIRM required)
- `POST /api/poweruser/backup-services` - Backup services to JSON
- `GET /api/poweruser/list-service-backups` - List available service backups
- `POST /api/poweruser/restore-services` - Restore services (CONFIRM required)
- `POST /api/poweruser/delete-all-services` - Delete all services (CONFIRM required)
- `POST /api/poweruser/backup-networks` - Backup networks to JSON
- `GET /api/poweruser/list-network-backups` - List available network backups
- `POST /api/poweruser/restore-networks` - Restore networks (CONFIRM required)
- `POST /api/poweruser/delete-all-networks` - Delete all networks (CONFIRM required)

### Backup File Storage
All backup files are stored in the `output/` folder with timestamp naming:
- `service_policies_backup_YYYYMMDD_HHMMSS.json`
- `services_backup_YYYYMMDD_HHMMSS.json`
- `networks_backup_YYYYMMDD_HHMMSS.json`

### Template Management Operations
- **Unassign All Templates**: Removes gateway_template assignments from all sites
- **Delete All Templates**: Deletes all gateway templates and device profiles
- **Unassign Templated Devices**: Moves gateway devices to unassigned inventory

## Container Development Workflow

**CRITICAL**: Anytime changes are made to configuration files, Python scripts, templates, or static assets, you MUST rebuild and relaunch any running containers to apply the changes:

```bash
# Podman
podman stop mist-cisco-converter
podman rm mist-cisco-converter
podman build --format docker -t mist-cisco-converter .
podman run -d --name mist-cisco-converter -p 8000:8000 --env-file .env -v ./data:/app/data:Z -v ./input:/app/input:Z mist-cisco-converter

# Docker
docker stop mist-cisco-converter
docker rm mist-cisco-converter
docker build -t mist-cisco-converter -f Containerfile .
docker run -d --name mist-cisco-converter -p 8000:8000 --env-file .env -v ./data:/app/data -v ./input:/app/input mist-cisco-converter
```

**Volume Mounts Required**:
- `./data:/app/data` - Application logs and output files
- `./input:/app/input` - Cisco configuration files to convert
- `--env-file .env` - Environment variables (API tokens, org_id, theme, etc.)

This ensures the container image includes all code changes. Static files and templates are baked into the image at build time.

## When in Doubt
1. **Check existing patterns** - grep for similar operations
2. **Validate early, return early** - NASA/JPL defensive programming
3. **Test in venv** - Windows 11 local development standard
4. **Rebuild containers** - Code changes require container rebuild
5. **Update docs** - README changelog

---

**Remember**: This codebase prioritizes safety and operational clarity over clever abstractions. Explicit > Implicit. Readable > Concise. Safe > Fast.
```
