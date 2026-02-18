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

## Container Development Workflow

**CRITICAL**: Anytime changes are made to configuration files, Python scripts, templates, or static assets, you MUST rebuild and relaunch any running containers to apply the changes:

```bash
# Podman
podman stop mist-cisco-converter
podman rm mist-cisco-converter
podman build --format docker -t mist-cisco-converter .
podman run -d --name mist-cisco-converter -p 8000:8000 --env-file .env -v ./data:/app/data:Z mist-cisco-converter

# Docker
docker stop mist-cisco-converter
docker rm mist-cisco-converter
docker build -t mist-cisco-converter -f Containerfile .
docker run -d --name mist-cisco-converter -p 8000:8000 --env-file .env -v ./data:/app/data mist-cisco-converter
```

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
