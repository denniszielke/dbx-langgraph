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

> Verified against the public repos [microsoft/Agent365-Samples](https://github.com/microsoft/Agent365-Samples), [microsoft/Agent365-python](https://github.com/microsoft/Agent365-python), and [microsoft/Agent365-devTools](https://github.com/microsoft/Agent365-devTools) (the CLI source). All are in active development with beta/prerelease packages, so re-check command names and package versions against those repos before scripting them into automation.

## 1. Background: how identity ties this repo to Agent 365

Microsoft Agent 365 is built **on top of Microsoft Entra Agent ID**. Every agent registered in Agent 365's Agent Registry is backed by:

- an **agent identity blueprint** (`microsoft.graph.agentIdentityBlueprint`) — a reusable template for a class of agents
- an **agent identity** (`microsoft.graph.agentIdentity`, a service principal) — the concrete, auditable identity for one running agent instance, with its own credentials, permissions, and sign-in/activity logs

This template already provisions exactly those two objects via `scripts/create_agent_identity.py` (the `create-agent-identity` command — see the **quickstart** skill for local setup). The same identity created for Foundry A2A authentication is the identity Agent 365 registers and can subsequently display in its **Agent Registry**.

Important distinction confirmed against the Agent 365 SDK repos: **creating the Entra identity is not the same as getting agent "action" telemetry into Agent 365.** Two separate things ride on this identity:

- **Registry/identity metadata** (blueprint, permissions, consent, sign-ins) — visible once the identity is registered in Agent 365, no extra code needed.
- **Agent/tool/LLM-call telemetry** ("actions") — only shows up if the agent process is instrumented with the **Microsoft Agent 365 SDK's observability package**, described in section 4.

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

Microsoft publishes an official **Agent 365 DevTools CLI** ([microsoft/Agent365-devTools](https://github.com/microsoft/Agent365-devTools)) as a supported alternative to hand-rolling Graph API calls like `create-agent-identity` does. It is a .NET global tool:

```bash
# Requires .NET 8.0+
dotnet tool install -g Microsoft.Agents.A365.DevTools.Cli --prerelease
a365 -h
```

Before first use, the CLI needs **its own custom Entra ID app registration** with **delegated** (not application) Microsoft Graph permissions, granted admin consent — this is separate from, and in addition to, the agent identity blueprint/identity objects it manages on your behalf. See the CLI repo's "Custom Client App Registration" setup guide for the exact permission set.

Key commands relevant to this template:

| Command | Purpose |
|---|---|
| `a365 setup` | Creates Azure resources, configures permissions, and registers your agent blueprint for deployment — covers the same ground as `create-agent-identity`, driven interactively/declaratively instead of via the Graph script |
| `a365 query-entra` | Query Microsoft Entra ID for agent information (scopes, permissions, consent status) — use this to check whether/how an identity is registered |
| `a365 publish` | Update agent manifest IDs and package the manifest for upload to the Microsoft 365 Admin Center (then "hire" the agent through Teams to complete onboarding) |
| `a365 cleanup` | Deletes all resources (blueprint, instance, other Azure resources) it created |
| `a365 develop` / `a365 develop-mcp` | Manage MCP tool servers for local agent development (including Dataverse-hosted MCP servers) |
| `a365 logs` | Export redacted CLI diagnostic logs safe to share with Microsoft support |

For this template, either path works — `a365 setup` if you want an interactive, opinionated flow and don't mind installing the .NET SDK, or `uv run create-agent-identity` if you want a lightweight Python/Graph-only flow with no additional runtime dependency. Don't run both against the same display name without checking `a365 query-entra` first, to avoid creating duplicate/orphaned blueprint objects.

## 4. Viewing the agent — and its actions — in Agent 365

Once the agent identity is registered in Agent 365 (via `a365 setup`/`a365 publish` or manual admin-center registration of the identity `create-agent-identity` produced), the **registry and identity metadata** show up immediately:

1. Open the **Agent 365 admin center** for the tenant that matches `ENTRA_TENANT_ID`.
2. Go to the **Agent Registry** and locate the agent by the `--display-name` you used in `create-agent-identity` / `a365 setup`.
3. Its detail page shows identity metadata (the same `appId`/object ID and credential info produced during provisioning), permissions/consent status, and sign-in activity for that identity. You can cross-check this from the CLI with `a365 query-entra`.

**Seeing agent *actions* (tool calls, LLM inference, agent turns) is a separate, opt-in step** — it does **not** happen automatically just because the identity exists. Agent 365 gets action-level telemetry only from agents instrumented with the **Microsoft Agent 365 observability SDK**, which is OpenTelemetry-based:

```bash
pip install microsoft-agents-a365-observability-core
```

```python
from microsoft_agents_a365.observability.core import configure

# Call after any existing OTel SDK setup (e.g. azure-monitor-opentelemetry, or a manual
# TracerProvider + OTLPSpanExporter) — configure() attaches its processors to the
# existing TracerProvider rather than replacing it.
configure(
    service_name="agent-langgraph-foundry",
    service_namespace="dbx-langgraph",
    token_resolver=my_token_resolver,  # resolves a token for the Agent 365 backend
)
```

Two env vars gate whether spans are produced/exported at all — the SDK **silently emits zero spans** if these are missing:

- `ENABLE_OBSERVABILITY=true` (or `ENABLE_A365_OBSERVABILITY_EXPORTER=true`, depending on SDK version — check the `.env.template` in the [`python/observability-with-otlp`](https://github.com/microsoft/Agent365-Samples/tree/main/python/observability-with-otlp) sample) enables span creation.
- A working `token_resolver` is required for spans to actually reach the Agent 365 backend (without one, `configure()` falls back to a local `ConsoleSpanExporter`).

**This template does not currently ship this instrumentation.** `agent_server/a2a_client.py` is a raw `httpx`-based A2A client — it isn't built on the OpenAI Agents SDK, LangChain, Semantic Kernel, or Microsoft Agent Framework, so none of the four auto-instrumentation extension packages (`microsoft-agents-a365-observability-extensions-{openai,langchain,semantickernel,agentframework}`) apply out of the box. If you want this agent's Foundry calls to show up as Agent 365 spans, wrap the request/response handling in `agent_server/a2a_client.py` with the SDK's manual scopes — `InvokeAgentScope` around a full turn, `InferenceScope` around each LLM call, `ExecuteToolScope` around each tool/action — following the pattern in the [`python/observability-with-otlp`](https://github.com/microsoft/Agent365-Samples/tree/main/python/observability-with-otlp) sample, which demonstrates exactly this "manual OTel + manual instrumentation" combination.

Until that instrumentation is added, treat the two observability surfaces as covering different things, not as duplicates of each other:

- **Agent 365 registry/identity view** — tenant-wide identity, permissions, consent, sign-in activity. Available today from the identity alone.
- **MLflow tracing (this repo)** — per-request trace of the Databricks app's own invoke/stream handling. Already configured (see `quickstart` / `run-locally` skills).
- **Agent 365 action/tool spans** — requires adding the observability SDK to `agent_server/`; not present by default in this template.

If the agent doesn't appear in the registry at all, it typically means the identity hasn't been registered/enrolled yet (section 3) — registration, not identity creation, is what makes it visible in Agent 365.

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
8. **Verify end-to-end**: query the deployed app (see README "Query your agent hosted on Databricks Apps"), then confirm the identity's sign-in activity shows up in the Agent 365 registry view (Entra side) for the same `ENTRA_AGENT_CLIENT_ID`, and that requests are traced in MLflow (Databricks side). Action-level spans will only appear in Agent 365 once the observability SDK from section 4 is added to `agent_server/`.

For app-binding errors ("An app with the same name already exists") or provider drift errors during deploy, see the **deploy** skill and the README "Common Issues" section — those are unrelated to identity/Agent 365 setup and are handled the same way regardless.

## Related skills

- **quickstart** — local environment + `.env` setup, including running `create-agent-identity`
- **deploy** — Databricks Asset Bundle deploy mechanics, binding existing apps, troubleshooting deploy errors
- **run-locally** — testing the agent and inspecting MLflow traces locally before deploying

## Reference repos (verified)

- [microsoft/Agent365-Samples](https://github.com/microsoft/Agent365-Samples) — sample agents and prompts across C#/.NET, Python, Node.js/TypeScript, and Salesforce/Apex, including the `python/observability-with-otlp` and `python/observability-with-azure-monitor` samples referenced above
- [microsoft/Agent365-python](https://github.com/microsoft/Agent365-python) — source for `microsoft-agents-a365-observability-core` and related Python packages (notifications, runtime, tooling, framework extensions)
- [microsoft/Agent365-devTools](https://github.com/microsoft/Agent365-devTools) — source for the `a365` DevTools CLI (`Microsoft.Agents.A365.DevTools.Cli`)
- [microsoft/Agent365-dotnet](https://github.com/microsoft/Agent365-dotnet) / [microsoft/Agent365-nodejs](https://github.com/microsoft/Agent365-nodejs) — equivalent SDKs for .NET and Node.js/TypeScript agents
