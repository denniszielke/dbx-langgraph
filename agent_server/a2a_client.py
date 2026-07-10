import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator
from uuid import uuid4

import httpx
import msal


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class EntraAgentAuthConfig:
    tenant_id: str
    client_id: str
    scope: str
    client_secret: str | None = None
    certificate_path: str | None = None
    certificate_thumbprint: str | None = None
    certificate_password: str | None = None
    federated_token_file: str | None = None

    @classmethod
    def from_env(cls) -> "EntraAgentAuthConfig":
        config = cls(
            tenant_id=_require_env("ENTRA_TENANT_ID"),
            client_id=_require_env("ENTRA_AGENT_CLIENT_ID"),
            scope=_require_env("FOUNDRY_A2A_SCOPE"),
            client_secret=os.getenv("ENTRA_AGENT_CLIENT_SECRET"),
            certificate_path=os.getenv("ENTRA_AGENT_CLIENT_CERTIFICATE_PATH"),
            certificate_thumbprint=os.getenv("ENTRA_AGENT_CLIENT_CERTIFICATE_THUMBPRINT"),
            certificate_password=os.getenv("ENTRA_AGENT_CLIENT_CERTIFICATE_PASSWORD"),
            federated_token_file=os.getenv("ENTRA_AGENT_FEDERATED_TOKEN_FILE"),
        )
        if not any(
            [
                config.client_secret,
                config.certificate_path and config.certificate_thumbprint,
                config.federated_token_file,
            ]
        ):
            raise ValueError(
                "Configure one Entra Agent ID credential via ENTRA_AGENT_CLIENT_SECRET, "
                "ENTRA_AGENT_CLIENT_CERTIFICATE_PATH + ENTRA_AGENT_CLIENT_CERTIFICATE_THUMBPRINT, "
                "or ENTRA_AGENT_FEDERATED_TOKEN_FILE."
            )
        return config


class FoundryA2AClient:
    def __init__(
        self,
        endpoint: str,
        agent_name: str,
        auth: EntraAgentAuthConfig,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.agent_name = agent_name
        self._auth = auth
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        self._token_lock = asyncio.Lock()
        self._cached_token: str | None = None
        self._cached_token_expires_at = 0.0
        self._msal_app = self._build_msal_app()

    @classmethod
    def from_env(cls) -> "FoundryA2AClient":
        return cls(
            endpoint=_require_env("FOUNDRY_A2A_ENDPOINT"),
            agent_name=_require_env("FOUNDRY_AGENT_NAME"),
            auth=EntraAgentAuthConfig.from_env(),
        )

    def _build_msal_app(self) -> msal.ConfidentialClientApplication | None:
        if self._auth.federated_token_file:
            return None

        client_credential: str | dict[str, str]
        if self._auth.client_secret:
            client_credential = self._auth.client_secret
        else:
            assert self._auth.certificate_path is not None
            assert self._auth.certificate_thumbprint is not None
            with open(self._auth.certificate_path, encoding="utf-8") as handle:
                pem_bundle = handle.read()
            client_credential = {
                "private_key": pem_bundle,
                "thumbprint": self._auth.certificate_thumbprint,
                **(
                    {"passphrase": self._auth.certificate_password}
                    if self._auth.certificate_password
                    else {}
                ),
            }

        return msal.ConfidentialClientApplication(
            client_id=self._auth.client_id,
            authority=f"https://login.microsoftonline.com/{self._auth.tenant_id}",
            client_credential=client_credential,
        )

    async def get_access_token(self) -> str:
        if self._cached_token and time.time() < self._cached_token_expires_at - 60:
            return self._cached_token

        async with self._token_lock:
            if self._cached_token and time.time() < self._cached_token_expires_at - 60:
                return self._cached_token

            token_result = await self._acquire_access_token()
            access_token = token_result.get("access_token")
            if not access_token:
                raise RuntimeError(
                    "Failed to acquire access token for Entra Agent ID authentication: "
                    f"{token_result.get('error')}: {token_result.get('error_description')}"
                )

            expires_in = int(token_result.get("expires_in") or 3600)
            self._cached_token = access_token
            self._cached_token_expires_at = time.time() + expires_in
            return access_token

    async def _acquire_access_token(self) -> dict[str, Any]:
        if self._auth.federated_token_file:
            return await self._acquire_federated_token()

        assert self._msal_app is not None
        return await asyncio.to_thread(
            self._msal_app.acquire_token_for_client,
            scopes=[self._auth.scope],
        )

    async def _acquire_federated_token(self) -> dict[str, Any]:
        assert self._auth.federated_token_file is not None
        token_endpoint = (
            f"https://login.microsoftonline.com/{self._auth.tenant_id}/oauth2/v2.0/token"
        )
        with open(self._auth.federated_token_file, encoding="utf-8") as handle:
            assertion = handle.read().strip()

        response = await self._http.post(
            token_endpoint,
            data={
                "client_id": self._auth.client_id,
                "grant_type": "client_credentials",
                "scope": self._auth.scope,
                "client_assertion_type": (
                    "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                ),
                "client_assertion": assertion,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self._raise_for_status(response, "Failed to exchange the federated token")
        return response.json()

    async def send_message(self, message: dict[str, Any], context_id: str | None) -> dict[str, Any]:
        response = await self._http.post(
            self.endpoint,
            headers=await self._request_headers(stream=False),
            json=self._json_rpc_payload(
                method="message/send",
                message=message,
                context_id=context_id,
            ),
        )
        self._raise_for_status(response, "Remote Foundry A2A request failed")
        payload = response.json()
        self._raise_for_json_rpc_error(payload)
        return payload

    async def stream_message(
        self,
        message: dict[str, Any],
        context_id: str | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with self._http.stream(
            "POST",
            self.endpoint,
            headers=await self._request_headers(stream=True),
            json=self._json_rpc_payload(
                method="message/stream",
                message=message,
                context_id=context_id,
            ),
        ) as response:
            self._raise_for_status(response, "Remote Foundry A2A streaming request failed")

            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                payload = await response.aread()
                if not payload:
                    return
                json_payload = json.loads(payload)
                self._raise_for_json_rpc_error(json_payload)
                yield json_payload
                return

            buffer: list[str] = []
            async for line in response.aiter_lines():
                if line == "":
                    payload = self._parse_sse_payload(buffer)
                    buffer = []
                    if payload is None:
                        continue
                    self._raise_for_json_rpc_error(payload)
                    yield payload
                    continue
                buffer.append(line)

            payload = self._parse_sse_payload(buffer)
            if payload is not None:
                self._raise_for_json_rpc_error(payload)
                yield payload

    async def _request_headers(self, stream: bool) -> dict[str, str]:
        access_token = await self.get_access_token()
        headers = {
            "Authorization": "Bearer " + access_token,
            "Content-Type": "application/json",
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    def _json_rpc_payload(
        self,
        method: str,
        message: dict[str, Any],
        context_id: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "message": message,
            "metadata": {"foundryAgentName": self.agent_name},
        }
        if context_id:
            params["contextId"] = context_id
        return {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": method,
            "params": params,
        }

    def _parse_sse_payload(self, lines: list[str]) -> dict[str, Any] | None:
        if not lines:
            return None

        data_lines: list[str] = []
        for line in lines:
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
            elif line.startswith(":"):
                continue
            elif line.lstrip().startswith("{"):
                data_lines.append(line.strip())

        if not data_lines:
            return None

        raw = "\n".join(data_lines).strip()
        if not raw or raw == "[DONE]":
            return None

        return json.loads(raw)

    def _raise_for_json_rpc_error(self, payload: dict[str, Any]) -> None:
        error = payload.get("error")
        if error:
            raise RuntimeError(
                "Foundry A2A endpoint returned a JSON-RPC error: "
                f"{json.dumps(error, sort_keys=True)}"
            )

    def _raise_for_status(self, response: httpx.Response, message: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:2000]
            raise RuntimeError(
                f"{message} (status={exc.response.status_code}): {body}"
            ) from exc
