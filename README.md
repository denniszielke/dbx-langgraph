# Responses API Agent

This template defines a conversational agent app. The app comes with a built-in chat UI, but also exposes an API endpoint for invoking the agent so that you can serve your UI elsewhere (e.g. on your website or in a mobile app).

The agent in this template implements the [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses) interface, but it now runs as a pure passthrough. Instead of running a local LLM or LangGraph loop, every incoming request is forwarded to a remote Microsoft Foundry hosted agent over its A2A endpoint and the remote response is translated back into MLflow `ResponsesAgent` events.

The agent input and output format are defined by MLflow's ResponsesAgent interface, which closely follows the [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses) interface. See [the MLflow docs](https://mlflow.org/docs/latest/genai/flavors/responses-agent-intro/) for input and output formats for streaming and non-streaming requests, tracing requirements, and other agent authoring details.

## Foundry A2A passthrough mode

This repository is configured for a **no-local-model** deployment model:

- Databricks hosts the API surface (`/invocations`, `/responses`, chat UI, MLflow tracing)
- `agent_server/agent.py` forwards each request directly to a remote Foundry A2A endpoint
- `agent_server/a2a_client.py` authenticates with **Microsoft Entra Agent ID** credentials and calls the remote hosted agent
- streamed A2A events are converted back into MLflow `ResponsesAgentStreamEvent` text deltas plus a final output item

Compared with the default LangGraph template, there is **no `ChatDatabricks` model client, no local agent loop, and no tool execution inside Databricks**. Databricks is only acting as the passthrough boundary and deployment host.

Reference documentation:

- [Enable incoming A2A on a Foundry agent](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint)
- [Autonomous authentication and authorization flow for Entra Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/autonomous-agent-authentication-authorization-flow)
- [Create or delete Entra Agent identities](https://learn.microsoft.com/en-us/entra/agent-id/create-delete-agent-identities)
- [Microsoft Entra Agent ID overview](https://learn.microsoft.com/en-us/entra/agent-id/)

## Build with AI Assistance

We recommend using AI coding assistants (Claude Code, Cursor, GitHub Copilot) to customize and deploy this template. Agent Skills in `.claude/skills/` provide step-by-step guidance for common tasks like setup, adding tools, and deployment. These skills are automatically detected by Claude, Cursor, and GitHub Copilot.

## Quick start

Run the `uv run quickstart` script to quickly set up your local environment and start the agent server. At any step, if there are issues, refer to the manual local development loop setup below.

This script will:

1. Verify uv, nvm, and Databricks CLI installations
2. Configure Databricks authentication
3. Configure agent tracing, by creating and linking an MLflow experiment to your app
4. Start the agent server and chat app

```bash
uv run quickstart
```

After the setup is complete, you can start the agent server and the chat app locally with:

```bash
uv run start-app
```

This will start the agent server and the chat app at http://localhost:8000.

**Next steps**: see [modifying your agent](#modifying-your-agent) to customize and iterate on the agent code.

## Manual local development loop setup

1. **Set up your local environment**
   Install `uv` (python package manager), `nvm` (node version manager), and the Databricks CLI:

   - [`uv` installation docs](https://docs.astral.sh/uv/getting-started/installation/)
   - [`nvm` installation](https://github.com/nvm-sh/nvm?tab=readme-ov-file#installing-and-updating)
     - Run the following to use Node 20 LTS:
       ```bash
       nvm use 20
       ```
   - [`databricks CLI` installation](https://docs.databricks.com/aws/en/dev-tools/cli/install)

2. **Set up local authentication to Databricks**

   In order to access Databricks resources from your local machine while developing your agent, you need to authenticate with Databricks. Choose one of the following options:

   **Option 1: OAuth via Databricks CLI (Recommended)**

   Authenticate with Databricks using the CLI. See the [CLI OAuth documentation](https://docs.databricks.com/aws/en/dev-tools/cli/authentication#oauth-user-to-machine-u2m-authentication).

   ```bash
   databricks auth login
   ```

   Set the `DATABRICKS_CONFIG_PROFILE` environment variable in your .env file to the profile you used to authenticate:

   ```bash
   DATABRICKS_CONFIG_PROFILE="DEFAULT" # change to the profile name you chose
   ```

   **Option 2: Personal Access Token (PAT)**

   See the [PAT documentation](https://docs.databricks.com/aws/en/dev-tools/auth/pat#databricks-personal-access-tokens-for-workspace-users).

   ```bash
   # Add these to your .env file
   DATABRICKS_HOST="https://host.databricks.com"
   DATABRICKS_TOKEN="dapi_token"
   ```

   See the [Databricks SDK authentication docs](https://docs.databricks.com/aws/en/dev-tools/sdk-python#authenticate-the-databricks-sdk-for-python-with-your-databricks-account-or-workspace).

3. **Create and link an MLflow experiment to your app**

   Create an MLflow experiment to enable tracing and version tracking. This is automatically done by the `uv run quickstart` script.

   Create the MLflow experiment via the CLI:

   ```bash
   DATABRICKS_USERNAME=$(databricks current-user me | jq -r .userName)
   databricks experiments create-experiment /Users/$DATABRICKS_USERNAME/agents-on-apps
   ```

   Make a copy of `.env.example` to `.env` and update the `MLFLOW_EXPERIMENT_ID` in your `.env` file with the experiment ID you created. The `.env` file will be automatically loaded when starting the server.

   ```bash
   cp .env.example .env
   # Edit .env and fill in your experiment ID
   ```

   See the [MLflow experiments documentation](https://docs.databricks.com/aws/en/mlflow/experiments#create-experiment-from-the-workspace).

4. **Provision an Entra Agent ID credential for the Foundry A2A target**

   Before you can run this passthrough agent, create an Entra Agent ID and an autonomous credential for it:

   ```bash
   uv run create-agent-identity --display-name agent-langgraph-foundry
   ```

   The script creates:

   - an **agent identity blueprint**
   - an **agent identity**
   - either a **certificate** credential (default) or a **client secret** fallback

   In the default certificate mode it writes the exact `ENTRA_*` values into `.agent-identity.env` (chmod `600`) and prints the non-secret values you need to copy into `.env`. If you choose the client-secret fallback, the script intentionally avoids writing the secret to disk; store it directly in a Databricks secret scope or use certificate mode for a file-based local credential. For deploys, store secrets in a Databricks secret scope and wire them into `databricks.yml`.

   > Prerequisite: authenticate first with `az login` or another credential source supported by `DefaultAzureCredential`, and ensure you have the Graph permissions required by the Agent ID beta APIs.

5. **Configure the remote Foundry A2A endpoint**

   Update `.env` with:

   - `FOUNDRY_A2A_ENDPOINT` — the hosted agent A2A endpoint, usually ending in `/endpoint/protocols/a2a`
   - `FOUNDRY_AGENT_NAME` — the remote agent name or id for logging/tracing
   - `FOUNDRY_A2A_SCOPE` — the Entra resource scope accepted by the remote A2A endpoint, typically `api://<remote-agent-app-id>/.default`
   - `ENTRA_TENANT_ID`, `ENTRA_AGENT_CLIENT_ID`, and one of the supported credential settings (`ENTRA_AGENT_CLIENT_SECRET`, or the certificate/federated equivalents)

6. **Test your agent locally**

   Start up the agent server and chat UI locally:

   ```bash
   uv run start-app
   ```

   Query your agent via the UI (http://localhost:8000) or REST API:

   **Advanced server options:**

   ```bash
   uv run start-server --reload   # hot-reload the server on code changes
   uv run start-server --port 8001 # change the port the server listens on
   uv run start-server --workers 4 # run the server with multiple workers
   ```

   - Example streaming request:
     ```bash
     curl -X POST http://localhost:8000/invocations \
     -H "Content-Type: application/json" \
     -d '{ "input": [{ "role": "user", "content": "hi" }], "stream": true }'
     ```
   - Example non-streaming request:
     ```bash
     curl -X POST http://localhost:8000/invocations  \
     -H "Content-Type: application/json" \
     -d '{ "input": [{ "role": "user", "content": "hi" }] }'
     ```

## Provisioning the Entra Agent ID blueprint & Microsoft Agent 365 observability

The `create-agent-identity` command (used in step 4 above) creates two Entra objects that back this agent's identity:

- an **agent identity blueprint** (`microsoft.graph.agentIdentityBlueprint`) — a reusable template for a class of agents
- an **agent identity** (`microsoft.graph.agentIdentity`) — the concrete, auditable identity used for the Foundry A2A calls

These are the same objects that **Microsoft Agent 365** — Microsoft's control plane for registering, securing, and observing AI agents — uses to populate its **Agent Registry**. You don't need a second identity: register the identity created by `create-agent-identity` into Agent 365 rather than creating a new one.

Microsoft also ships an official **Agent 365 DevTools CLI** (`a365`, from [microsoft/Agent365-devTools](https://github.com/microsoft/Agent365-devTools), installed via `dotnet tool install -g Microsoft.Agents.A365.DevTools.Cli --prerelease`) as an alternative to the Graph-based `create-agent-identity` script — its `a365 setup` command creates Azure resources and registers the agent blueprint, and `a365 query-entra` checks scopes/permissions/consent status for an existing identity.

Once registered, you can see the agent's **identity and registry metadata** from the Agent 365 side immediately:

1. Open the Agent 365 admin center for the tenant matching `ENTRA_TENANT_ID`.
2. Find the agent in the **Agent Registry** by the `--display-name` used with `create-agent-identity`.
3. Its detail page shows identity metadata, permissions/consent status, and sign-in activity for that identity.

Seeing agent **actions** (tool calls, LLM inference) in Agent 365 is a separate, opt-in step: it requires instrumenting the agent process with the `microsoft-agents-a365-observability-core` Python SDK (OpenTelemetry-based, from [microsoft/Agent365-python](https://github.com/microsoft/Agent365-python)) — it does not happen automatically just because the identity is registered. This template does not ship that instrumentation by default; pair the identity/registry view with the MLflow tracing already configured for this app (see [Manual local development loop setup](#manual-local-development-loop-setup) step 3) for per-request detail on how the Databricks app itself handled a call.

For the full identity → deploy → verify workflow, including secret-scope wiring and where to check status on both the Databricks and Entra sides, see the **agent-365-observability** skill at `.claude/skills/agent-365-observability/SKILL.md`.

## Modifying your agent

This template is now centered on the Foundry passthrough path:

- `agent_server/agent.py` keeps the MLflow `@invoke()` / `@stream()` handlers
- `agent_server/a2a_client.py` handles Entra Agent ID auth plus the outbound A2A HTTP calls
- `agent_server/utils.py` converts between MLflow Responses input/output shapes and A2A payloads

If you need to change the forwarding behavior, update those files rather than adding a local model loop.

Required files for hosting with MLflow `AgentServer`:

- `agent.py`: Contains the passthrough handler logic that forwards requests to the remote Foundry A2A agent
- `start_server.py`: Initializes and runs the MLflow `AgentServer` with agent_type="ResponsesAgent". You don't have to modify this file for most common use cases, but can add additional server routes (e.g. a `/metrics` endpoint) here

**Common customization questions:**

**Q: Can I add additional files or folders to my agent?**
Yes. Add additional files or folders as needed. Ensure the script within `pyproject.toml` runs the correct script that starts the server and sets up MLflow tracing.

**Q: How do I add dependencies to my agent?**
Run `uv add <package_name>` (e.g., `uv add "mlflow-skinny[databricks]"`). See the [python pyproject.toml guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#dependencies-and-requirements).

**Q: Can I add custom tracing beyond the built-in tracing?**
Yes. This template uses MLflow's agent server, which comes with automatic tracing for agent logic decorated with `@invoke()` and `@stream()`. It also uses [MLflow autologging APIs](https://mlflow.org/docs/latest/genai/tracing/#one-line-auto-tracing-integrations) to capture traces from LLM invocations. However, you can add additional instrumentation to capture more granular trace information when your agent runs. See the [MLflow tracing documentation](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/app-instrumentation/).

**Q: How can I extend this example with additional tools and capabilities?**
The intended extension point is the remote Foundry agent. Add tools, instructions, or orchestration there, and let Databricks continue forwarding requests over A2A.

## Evaluating your agent

Evaluate your agent by calling the invoke function you defined for the agent locally.

- Update your `evaluate_agent.py` file with the preferred evaluation dataset and scorers.

Run the evaluation using the evaluation script:

```bash
uv run agent-evaluate
```

After it completes, open the MLflow UI link for your experiment to inspect results.

## Deploying to Databricks Apps

This template uses [Databricks Asset Bundles (DABs)](https://docs.databricks.com/aws/en/dev-tools/bundles/) for deployment. The `databricks.yml` file defines the app configuration and resource permissions.

> **`app.yaml` vs `databricks.yml`**: `app.yaml` is used when deploying via `databricks apps deploy` (manual path). When deploying via DABs (`databricks bundle deploy`), the `config:` section in `databricks.yml` takes precedence. If you change environment variables or the start command, update `databricks.yml` — that's what DABs reads.

Ensure you have the [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/tutorial) installed and configured.

1. **Run the pre-flight check**

   Start the agent locally, send a test request, and verify the response to catch configuration and code errors early:

   ```bash
   uv run preflight
   ```

2. **Validate the bundle configuration**

   Catch any configuration errors before deploying:

   ```bash
   databricks bundle validate
   ```

   Before deploying, make sure the Foundry A2A env vars in `databricks.yml` are filled in and that any sensitive `ENTRA_*` value is referenced from a Databricks secret scope instead of an inline literal.

3. **Deploy the bundle**

   This uploads your code and configures resources (MLflow experiment, serving endpoints, etc.) defined in `databricks.yml`:

   ```bash
   databricks bundle deploy
   ```

4. **Start or restart the app**

   ```bash
   databricks bundle run agent_langgraph
   ```

   > **Note:** `bundle deploy` only uploads files and configures resources. `bundle run` is **required** to actually start/restart the app with the new code.

   To grant access to additional resources (serving endpoints, genie spaces, UC Functions, Vector Search), add them to `databricks.yml` and redeploy. See the [Databricks Apps resources documentation](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources).

   **On-behalf-of (OBO) User Authentication**: Use `get_user_workspace_client()` from `agent_server.utils` to authenticate as the requesting user instead of the app service principal. See the [OBO authentication documentation](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth?language=Streamlit#retrieve-user-authorization-credentials).

6. **Query your agent hosted on Databricks Apps**

   You must use a Databricks OAuth token to query agents hosted on Databricks Apps. See [Query an agent](https://docs.databricks.com/aws/en/generative-ai/agent-framework/query-agent) for full details.

   **Using the Databricks OpenAI client (Python):**

   ```bash
   uv pip install databricks-openai
   ```

   ```python
   from databricks.sdk import WorkspaceClient
   from databricks_openai import DatabricksOpenAI

   w = WorkspaceClient()
   client = DatabricksOpenAI(workspace_client=w)

   # Non-streaming
   response = client.responses.create(
       model="apps/<app-name>",
       input=[{"role": "user", "content": "hi"}],
   )
   print(response)

   # Streaming
   streaming_response = client.responses.create(
       model="apps/<app-name>",
       input=[{"role": "user", "content": "hi"}],
       stream=True,
   )
   for chunk in streaming_response:
       print(chunk)
   ```

   **Using curl:**

   ```bash
   # Generate an OAuth token
   databricks auth login --host <https://host.databricks.com>
   databricks auth token
   ```

   ```bash
   # Streaming request
   curl --request POST \
     --url <app-url>.databricksapps.com/responses \
     --header "Authorization: Bearer <oauth-token>" \
     --header "Content-Type: application/json" \
     --data '{
       "input": [{ "role": "user", "content": "hi" }],
       "stream": true
     }'
   ```

   ```bash
   # Non-streaming request
   curl --request POST \
     --url <app-url>.databricksapps.com/responses \
     --header "Authorization: Bearer <oauth-token>" \
     --header "Content-Type: application/json" \
     --data '{
       "input": [{ "role": "user", "content": "hi" }]
     }'
   ```

For future updates, run `databricks bundle deploy` and `databricks bundle run agent_langgraph` to redeploy.

### Common Issues

- **`databricks bundle deploy` fails with "An app with the same name already exists"**

  This happens when an app with the same name was previously created outside of DABs. To fix, bind the existing app to your bundle:

  ```bash
  # 1. Get the existing app's config (note the budget_policy_id if present)
  databricks apps get <app-name> --output json | jq '{name, budget_policy_id, description}'

  # 2. Update databricks.yml to include budget_policy_id if it was returned above

  # 3. Bind the existing app to your bundle
  databricks bundle deployment bind agent_langgraph <app-name> --auto-approve

  # 4. Deploy
  databricks bundle deploy
  ```

  Alternatively, delete the existing app and deploy fresh: `databricks apps delete <app-name>` (this permanently removes the app's URL and service principal).

- **`databricks bundle deploy` fails with "Provider produced inconsistent result after apply"**

  The existing app has server-side configuration (like `budget_policy_id`) that doesn't match your `databricks.yml`. Run `databricks apps get <app-name> --output json` and sync any missing fields to your `databricks.yml`.

- **App is running old code after `databricks bundle deploy`**

  `bundle deploy` only uploads files and configures resources. You must run `databricks bundle run agent_langgraph` to actually start/restart the app with the new code.

### FAQ

- For a streaming response, I see a 200 OK in the logs, but an error in the actual stream. What's going on?
  - This is expected behavior. The initial 200 OK confirms stream setup; streaming errors don't affect this status.
- When querying my agent, I get a 302 error. What's going on?
  - Use an OAuth token. PATs are not supported for querying agents.
