#!/usr/bin/env python3
"""
Provision or delete a Microsoft Entra Agent ID identity for Foundry A2A calls.

Prerequisites:
  - You are already authenticated to Microsoft Graph via az login, environment
    credentials, or another source supported by DefaultAzureCredential.
  - Your identity has the Graph permissions and Entra roles required by the
    Agent ID beta APIs (for example, the permissions documented in the
    Microsoft Graph agentIdentityBlueprint and agentIdentity API docs).
  - OpenSSL must be installed if you choose --credential-mode certificate.

Examples:
    uv run create-agent-identity --display-name agent-dbxtemplate
    uv run create-agent-identity --display-name agent-dbxtemplate --credential-mode secret
    uv run create-agent-identity --delete <agent-identity-object-id>
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential

GRAPH_BASE_URL = "https://graph.microsoft.com/beta"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def print_header(text: str) -> None:
    print(f"\n{'=' * 67}")
    print(text)
    print("=" * 67)


def print_success(text: str) -> None:
    print(f"✓ {text}")


def print_error(text: str) -> None:
    print(f"✗ {text}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or delete a Microsoft Entra Agent ID identity plus a credential "
            "for Foundry A2A passthrough authentication."
        )
    )
    parser.add_argument("--display-name", help="Display name for the new blueprint and agent identity")
    parser.add_argument("--tenant-id", help="Tenant ID to print in the resulting .env values")
    parser.add_argument(
        "--sponsor-object-id",
        help="Existing user or group object ID to bind as sponsor. Defaults to the signed-in user.",
    )
    parser.add_argument(
        "--credential-mode",
        choices=("certificate", "secret"),
        default="certificate",
        help="Preferred credential type for the agent identity (default: certificate).",
    )
    parser.add_argument(
        "--certificate-output-dir",
        default=".azure-agent-id",
        help="Directory where generated certificate files are written.",
    )
    parser.add_argument(
        "--credential-valid-days",
        type=int,
        default=365,
        help="Validity window in days for the generated secret or certificate.",
    )
    parser.add_argument(
        "--delete",
        metavar="AGENT_IDENTITY_OBJECT_ID",
        help="Delete an existing agent identity object instead of creating one.",
    )
    parser.add_argument(
        "--delete-blueprint-id",
        metavar="BLUEPRINT_OBJECT_ID",
        help="Also delete the associated agent identity blueprint object.",
    )
    return parser.parse_args()


class GraphClient:
    def __init__(self) -> None:
        credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=False,
        )
        token = credential.get_token(GRAPH_SCOPE)
        self.tenant_id = _tenant_id_from_access_token(token.token)
        self._http = httpx.Client(
            base_url=GRAPH_BASE_URL,
            headers={
                "Authorization": "Bearer " + token.token,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = self._http.request(method, path, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Graph API call failed for {method} {path}: {exc.response.text[:2000]}"
            ) from exc
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/me?$select=id,displayName,userPrincipalName")


def _tenant_id_from_access_token(access_token: str) -> str | None:
    try:
        payload = access_token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded)
        return claims.get("tid")
    except Exception:
        return None


def sponsor_bindings(sponsor_object_id: str) -> list[str]:
    return [f"https://graph.microsoft.com/v1.0/directoryObjects/{sponsor_object_id}"]


def create_blueprint(graph: GraphClient, display_name: str, sponsor_object_id: str) -> dict[str, Any]:
    payload = {
        "displayName": f"{display_name} Blueprint",
        "sponsors@odata.bind": sponsor_bindings(sponsor_object_id),
    }
    return graph.request("POST", "/applications/microsoft.graph.agentIdentityBlueprint", payload)


def create_agent_identity(
    graph: GraphClient,
    display_name: str,
    blueprint_app_id: str,
    sponsor_object_id: str,
) -> dict[str, Any]:
    payload = {
        "displayName": display_name,
        "agentIdentityBlueprintId": blueprint_app_id,
        "sponsors@odata.bind": sponsor_bindings(sponsor_object_id),
    }
    return graph.request("POST", "/servicePrincipals/microsoft.graph.agentIdentity", payload)


def get_agent_identity(graph: GraphClient, agent_identity_object_id: str) -> dict[str, Any]:
    return graph.request(
        "GET",
        f"/servicePrincipals/{agent_identity_object_id}"
        "?$select=id,appId,displayName,keyCredentials,passwordCredentials",
    )


def add_secret_credential(
    graph: GraphClient,
    agent_identity_object_id: str,
    valid_days: int,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    payload = {
        "passwordCredential": {
            "displayName": "Databricks Foundry A2A secret",
            "startDateTime": now.isoformat(),
            "endDateTime": (now + timedelta(days=valid_days)).isoformat(),
        }
    }
    return graph.request("POST", f"/servicePrincipals/{agent_identity_object_id}/addPassword", payload)


def _require_openssl() -> None:
    if shutil.which("openssl") is None:
        raise RuntimeError(
            "OpenSSL is required for --credential-mode certificate. "
            "Install openssl or rerun with --credential-mode secret."
        )


def add_certificate_credential(
    graph: GraphClient,
    agent_identity: dict[str, Any],
    output_dir: Path,
    valid_days: int,
) -> dict[str, str]:
    _require_openssl()
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = agent_identity["displayName"].replace(" ", "-").lower()
    key_path = output_dir / f"{safe_name}.key.pem"
    cert_path = output_dir / f"{safe_name}.cert.pem"
    bundle_path = output_dir / f"{safe_name}.pem"

    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            str(valid_days),
            "-nodes",
            "-subj",
            f"/CN={agent_identity['displayName']}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    bundle_path.write_text(
        key_path.read_text(encoding="utf-8") + cert_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    der_bytes = subprocess.run(
        ["openssl", "x509", "-outform", "der", "-in", str(cert_path)],
        check=True,
        capture_output=True,
    ).stdout
    thumbprint = (
        subprocess.run(
            ["openssl", "x509", "-fingerprint", "-sha1", "-noout", "-in", str(cert_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split("=", maxsplit=1)[1]
        .replace(":", "")
    )

    existing_keys = agent_identity.get("keyCredentials") or []
    now = datetime.now(UTC)
    payload = {
        "@odata.type": "#microsoft.graph.agentIdentity",
        "keyCredentials": existing_keys
        + [
            {
                "displayName": "Databricks Foundry A2A certificate",
                "startDateTime": now.isoformat(),
                "endDateTime": (now + timedelta(days=valid_days)).isoformat(),
                "key": base64.b64encode(der_bytes).decode("utf-8"),
                "type": "AsymmetricX509Cert",
                "usage": "Verify",
            }
        ],
    }
    graph.request("PATCH", f"/servicePrincipals/{agent_identity['id']}", payload)

    return {
        "bundle_path": str(bundle_path.resolve()),
        "thumbprint": thumbprint,
        "certificate_path": str(cert_path.resolve()),
        "private_key_path": str(key_path.resolve()),
    }


def delete_agent_identity(graph: GraphClient, agent_identity_object_id: str) -> None:
    graph.request(
        "DELETE",
        f"/servicePrincipals/{agent_identity_object_id}/microsoft.graph.agentIdentity",
    )


def delete_blueprint(graph: GraphClient, blueprint_object_id: str) -> None:
    graph.request(
        "DELETE",
        f"/applications/{blueprint_object_id}/microsoft.graph.agentIdentityBlueprint",
    )


def print_env_instructions(
    tenant_id: str,
    agent_identity: dict[str, Any],
    secret_credential: dict[str, Any] | None,
    certificate_credential: dict[str, str] | None,
    blueprint: dict[str, Any],
) -> None:
    print_header("Copy these values into /home/runner/work/dbx-langgraph/dbx-langgraph/.env")
    print(f"ENTRA_TENANT_ID={tenant_id}")
    print(f"ENTRA_AGENT_CLIENT_ID={agent_identity['appId']}")
    if secret_credential is not None:
        print(f"ENTRA_AGENT_CLIENT_SECRET={secret_credential['secretText']}")
    if certificate_credential is not None:
        print(
            f"ENTRA_AGENT_CLIENT_CERTIFICATE_PATH={certificate_credential['bundle_path']}"
        )
        print(
            "ENTRA_AGENT_CLIENT_CERTIFICATE_THUMBPRINT="
            f"{certificate_credential['thumbprint']}"
        )
    print("# Set this to the remote Foundry A2A resource scope, e.g. api://<remote-agent-app-id>/.default")
    print("FOUNDRY_A2A_SCOPE=")
    print()
    print("Helpful IDs:")
    print(f"  Blueprint object id : {blueprint['id']}")
    print(f"  Blueprint app id    : {blueprint['appId']}")
    print(f"  Agent object id     : {agent_identity['id']}")
    print(f"  Agent client id     : {agent_identity['appId']}")
    if certificate_credential is not None:
        print(f"  Certificate bundle  : {certificate_credential['bundle_path']}")
        print(f"  Public certificate  : {certificate_credential['certificate_path']}")
        print(f"  Private key         : {certificate_credential['private_key_path']}")


def main() -> None:
    args = parse_args()
    graph = GraphClient()

    if args.delete:
        print_header("Deleting Entra Agent ID identity")
        delete_agent_identity(graph, args.delete)
        print_success(f"Deleted agent identity {args.delete}")
        if args.delete_blueprint_id:
            delete_blueprint(graph, args.delete_blueprint_id)
            print_success(f"Deleted blueprint {args.delete_blueprint_id}")
        return

    if not args.display_name:
        raise SystemExit("--display-name is required when creating a new agent identity")

    sponsor_object_id = args.sponsor_object_id or graph.me()["id"]
    tenant_id = (
        args.tenant_id
        or graph.tenant_id
        or os.getenv("AZURE_TENANT_ID")
        or "<your-tenant-id>"
    )

    print_header("Creating Entra Agent ID blueprint")
    blueprint = create_blueprint(graph, args.display_name, sponsor_object_id)
    print_success(
        f"Created blueprint {blueprint['displayName']} (object={blueprint['id']}, appId={blueprint['appId']})"
    )

    print_header("Creating Entra Agent ID service identity")
    agent_identity = create_agent_identity(
        graph,
        args.display_name,
        blueprint["appId"],
        sponsor_object_id,
    )
    agent_identity = get_agent_identity(graph, agent_identity["id"])
    print_success(
        f"Created agent identity {agent_identity['displayName']} (object={agent_identity['id']}, clientId={agent_identity['appId']})"
    )

    secret_credential: dict[str, Any] | None = None
    certificate_credential: dict[str, str] | None = None
    if args.credential_mode == "certificate":
        certificate_credential = add_certificate_credential(
            graph,
            agent_identity,
            Path(args.certificate_output_dir),
            args.credential_valid_days,
        )
        print_success("Attached certificate credential to the agent identity")
    else:
        secret_credential = add_secret_credential(
            graph,
            agent_identity["id"],
            args.credential_valid_days,
        )
        print_success("Attached client secret to the agent identity")

    print_env_instructions(
        tenant_id=tenant_id,
        agent_identity=agent_identity,
        secret_credential=secret_credential,
        certificate_credential=certificate_credential,
        blueprint=blueprint,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print_error(str(exc))
        raise
