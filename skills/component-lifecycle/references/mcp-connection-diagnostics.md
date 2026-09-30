# MCP server connection diagnostics

> Based on a real debugging session against the jike MCP SSE server

---

## Symptom

After the Gateway starts, the MCP tools are unavailable, and the logs either show a connection failure or say nothing at all.

---

## Diagnostic flow (bottom-up)

### Layer 1: venv package check

```bash
# confirm whether the mcp package is in the venv
~/.hermes/hermes-agent/.venv/bin/python -c "from mcp.client.sse import sse_client; print('OK')"
```

- `ModuleNotFoundError` → the `mcp` package is not installed
- In pyproject.toml, `mcp` is in the `dev` extra, so a default `uv sync` does not install it
- Install with: `uv sync --extra mcp` + list every extra you already use in the same command (see "Known pitfalls" below)

### Layer 2: Gateway logs

```bash
journalctl --user -u hermes-gateway --no-pager | grep -iE 'mcp|server.*connect|initial connection'
```

- Entries present → the package is fine but the connection fails; look at the specific error code
- No entries → the package is missing; the Gateway silently skips every MCP server at the import stage

### Layer 3: endpoint connectivity test

```bash
# GET request (the SSE protocol handshakes with GET)
curl -s --max-time 5 -o /dev/null -w '%{http_code}' <MCP_URL>

# POST request (Streamable HTTP handshakes with POST)
curl -s -X POST --max-time 5 -o /dev/null -w '%{http_code}' <MCP_URL>

# OPTIONS probe
curl -s -X OPTIONS --max-time 5 -o /dev/null -w '%{http_code}' <MCP_URL>
```

- `200` → that HTTP method is available
- `405` → that HTTP method is not supported by the server

### Layer 4: protocol version mismatch diagnosis

MCP has two transports, and they use different HTTP methods:

| Transport | Handshake method | Configuration |
|---------|:------:|------|
| Streamable HTTP (new, default) | POST | no extra configuration needed |
| Legacy SSE protocol (old) | GET | requires `transport: sse` |

**How to tell:**
- GET 200 + POST 405 → the server supports only the legacy SSE protocol
- POST 200 + GET 200 → the server supports Streamable HTTP (the standard)

### Layer 5: source verification

```bash
# the transport options of the MCP client in the Hermes source
grep -n 'transport.*sse\|sse_client\|streamable_http' ~/.hermes/hermes-agent/tools/mcp_tool.py
```

Key point: if the target server speaks the legacy SSE protocol, add `transport: sse` to that server's entry in `config.yaml`.

---

## Fix template

```yaml
# SSE MCP server configuration in config.yaml
mcp_servers:
  my_sse_server:
    url: http://example.com/path/to/sse
    headers:
      Authorization: Bearer ***
    timeout: 60
    connect_timeout: 30
    transport: sse          # ← key: tells Hermes to handshake with GET
```

---

## Known pitfalls

1. **`uv sync --extra X` is replace mode, not append**: running it once uninstalls the other extras (48+ packages disappear). List every extra you need in a single command, or use `--all-extras`.

2. **Grep scope that is too narrow**: the MCP client code lives in `tools/mcp_tool.py`, not in `hermes_cli/`. Before asserting "package X is not needed", search the whole codebase.

3. **`config.yaml` is protected**: no agent mechanism (patch/sed/write_file) can modify it. The user has to be asked to edit it by hand.
