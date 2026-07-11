---
name: agent-365-observability
description: "Provision the Microsoft Entra Agent ID blueprint and connect this Databricks-hosted agent to Microsoft Agent 365 for identity, registry, and observability. Use when: (1) User asks about 'agent identity blueprint', 'agent 365', 'agent registry', or 'agent observability', (2) User wants to see agent actions/activity for an agent connected to their Databricks workspace, (3) User asks how the Agent 365 CLI relates to this template, (4) Planning end-to-end identity + deployment for the Foundry passthrough agent."
---

# Entra Agent ID Blueprint & Microsoft Agent 365 Observability

This skill covers the full lifecycle for this template's identity layer:

1. Provisioning the **Entra Agent ID blueprint** + agent identity (already automated by `create-agent-identity`)
2. Registering / surfacing that same agent inside **Microsoft Agent 365** (Microsoft's control plane for AI agents, announced at Ignite 2025) so you get a unified **agent registry**, **identity**, and **observability** dashboard
3. Using the **Agent 365 CLI** as an alternative/complementary path to the Graph-based script in this repo
4. Viewing the agent and its actions in Agent 365 once your Databricks workspace/agent is connected
5. Detailed guidance for deploying the agent on Databricks so it shows up correctly end-to-end

> Agent 365 is a fast-moving product surface. Always cross-check exact portal navigation and CLI command syntax against the current [Microsoft Entra Agent ID docs](https://learn.microsoft.com/en-us/entra/agent-id/) and Agent 365 admin center documentation before following steps verbatim — screens and command names may shift between previews.

## 1. Background: how identity ties this repo to Agent 365

Microsoft Agent 365 is built **on top of Microsoft Entra Agent ID**. Every agent registered in Agent 365's Agent Registry is backed by:

- an **agent identity blueprint** (`microsoft.graph.agentIdentityBlueprint`) — a reusable template for a class of agents
- an **agent identity** (`microsoft.graph.agentIdentity`, a service principal) — the concrete, auditable identity for one running agent instance, with its own credentials, permissions, and sign-in/activity logs

This template already provisions exactly those two objects via `scripts/create_agent_identity.py` (the `create-agent-identity` command — see the **quickstart** skill for local setup). Because the agent identity is a first-class Entra object, **the same identity created for Foundry A2A authentication is what Agent 365 discovers and displays** once the tenant's Agent 365 registry is enabled — you do not need a separate identity just for Agent 365.

## 2. Provisioning the agent identity blueprint (this repo)

```bash
uv run create-agent-identity --display-name agent-langgraph-foundry
```

This creates:

- the **agent identity blueprint** (`<display-name> Blueprint`)
- the **agent identity** bound to that blueprint
- a certificate credential (default) or client-secret credential (`--credential-mode secret`)

Useful flags:

| Flag | Purpose |
|---|---|
| `--display-name` | Name used for both the blueprint and the identity |
| `--sponsor-object-id` | User/group object ID to bind as sponsor (defaults to signed-in user) |
| `--credential-mode {certificate,secret}` | Credential type (default: certificate) |
| `--credential-valid-days` | Validity window for the credential |
| `--delete <agent-identity-object-id>` / `--delete-blueprint-id <blueprint-object-id>` | Tear down identity/blueprint objects |

Prerequisites: authenticate with `az login` (or another `DefaultAzureCredential` source) with the Graph permissions required for the Agent ID beta APIs, and `openssl` installed if using certificate mode. See `README.md` step 4 ("Provision an Entra Agent ID credential for the Foundry A2A target") for the full walkthrough and where the resulting `ENTRA_*` values are consumed by `agent_server/a2a_client.py`.

## 3. The Agent 365 CLI as an option

Microsoft ships an Agent 365 CLI (surfaced through the Microsoft 365 / Entra Agent ID admin tooling) as an alternative to calling the Graph API directly. Consider it when:

- You want to **enroll** an already-created agent identity into the Agent 365 registry without hand-rolling Graph calls.
- You need to run registry/enrollment operations from CI/CD (e.g., as part of the same pipeline that runs `databricks bundle deploy`), rather than through the admin portal UI.
- You want to script bulk **discovery** of shadow/unregistered agents across a tenant before onboarding this one.

Typical flow when using the CLI in addition to this repo's script:

1. Install and authenticate the CLI against the same tenant you used for `create-agent-identity` (it must resolve the same Entra tenant as `ENTRA_TENANT_ID` in your `.env`).
2. Use the CLI's registry/enroll command, passing the **agent identity's `appId`/object ID** created in step 2 above — do not create a second, disconnected identity.
3. Confirm enrollment succeeded by checking the agent's status in the Agent 365 admin center (or the CLI's status/get command).

> Because CLI command names are still evolving in preview, confirm the exact subcommands (install, login, register/enroll, status) against the current Microsoft Agent 365 CLI reference before scripting them into automation. If the CLI isn't available/enrolled in your tenant yet, the Graph-based `create-agent-identity` script plus manual registration through the Agent 365 admin center is a fully supported fallback.

## 4. Viewing the agent and its actions in Agent 365

Once the agent identity created in step 2 is registered/enrolled in Agent 365 (via the CLI or the admin center), you can see it and its activity from the Agent 365 side without any further Databricks-side configuration, because the identity — not the app — is what Agent 365 tracks:

1. Open the **Agent 365 admin center** (or the Agent 365 pane inside the Microsoft 365 admin center) for the tenant that matches `ENTRA_TENANT_ID`.
2. Go to the **Agent Registry** and locate the agent by the `--display-name` you used in `create-agent-identity` (e.g. `agent-langgraph-foundry`).
3. Open the agent's detail page to see:
   - **Identity details** — the same `appId`/object ID and credential metadata produced by `create-agent-identity`
   - **Activity / observability** — sign-ins, token issuance, and (where the remote Foundry agent emits them) tool/action telemetry for that identity
   - **Permissions and posture** — what the identity is scoped to access, useful for confirming least-privilege before wiring it into `databricks.yml` secrets
4. Because this template's agent runs as a **passthrough** to a remote Foundry A2A agent (`agent_server/a2a_client.py`), the "actions" you see against this identity in Agent 365 reflect calls authenticated with that identity — i.e., every request Databricks forwards to Foundry. For agent-turn-level detail (what the Databricks app itself received/returned), pair this with the MLflow tracing already configured for this app (see the `quickstart` and `run-locally` skills) — Agent 365 observability and MLflow tracing are complementary, not a replacement for each other:
   - **Agent 365** = tenant-wide identity, registry, and cross-agent governance view.
   - **MLflow tracing (this repo)** = per-request trace of the Databricks app's own invoke/stream handling.

If the agent doesn't appear in the registry yet, it typically means the identity hasn't been enrolled (see step 3) — enrollment, not identity creation, is what makes it visible in Agent 365.

## 5. Deploying this agent on Databricks (detailed)

Follow this order so the identity, the app, and the observability views all agree with each other:

1. **Provision the identity first** (section 2 above) — do this before deploying so `databricks.yml` can reference the final `ENTRA_*` secret values.
2. **Store secrets in a Databricks secret scope**, then reference them from `databricks.yml` (never inline the client secret or certificate contents):
   ```bash
   databricks secrets create-scope agent-identity
   databricks secrets put-secret agent-identity entra-client-secret
   ```
3. **Fill in the Foundry + Entra env vars** in `databricks.yml` / `.env`: `FOUNDRY_A2A_ENDPOINT`, `FOUNDRY_AGENT_NAME`, `FOUNDRY_A2A_SCOPE`, `ENTRA_TENANT_ID`, `ENTRA_AGENT_CLIENT_ID`, and the chosen credential (`ENTRA_AGENT_CLIENT_SECRET` or the certificate equivalents) — see README step 5.
4. **Pre-flight check locally**: `uv run preflight` to catch config errors before deploying.
5. **Validate the bundle**: `databricks bundle validate`.
6. **Deploy**: `databricks bundle deploy` (uploads code + provisions resources declared in `databricks.yml`).
7. **Start/restart the app**: `databricks bundle run agent_langgraph` — required after every deploy; `bundle deploy` alone does not restart the running app.
8. **Verify end-to-end**: query the deployed app (see README "Query your agent hosted on Databricks Apps"), then confirm the corresponding identity activity shows up both in MLflow tracing (Databricks side) and in the Agent 365 registry/activity view (Entra side) for the same `ENTRA_AGENT_CLIENT_ID`.

For app-binding errors ("An app with the same name already exists") or provider drift errors during deploy, see the **deploy** skill and the README "Common Issues" section — those are unrelated to identity/Agent 365 setup and are handled the same way regardless.

## Related skills

- **quickstart** — local environment + `.env` setup, including running `create-agent-identity`
- **deploy** — Databricks Asset Bundle deploy mechanics, binding existing apps, troubleshooting deploy errors
- **run-locally** — testing the agent and inspecting MLflow traces locally before deploying
