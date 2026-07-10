import json
import logging
from typing import Any, AsyncGenerator, AsyncIterator, Optional
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from mlflow.genai.agent_server import get_request_headers
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentStreamEvent,
    create_text_delta,
    create_text_output_item,
    to_chat_completions_input,
)


def get_session_id(request: ResponsesAgentRequest) -> str | None:
    if request.context and request.context.conversation_id:
        return request.context.conversation_id
    if request.custom_inputs and isinstance(request.custom_inputs, dict):
        return request.custom_inputs.get("session_id")
    return None


def get_user_workspace_client() -> WorkspaceClient:
    token = get_request_headers().get("x-forwarded-access-token")
    return WorkspaceClient(token=token, auth_type="pat")


def get_databricks_host_from_env() -> Optional[str]:
    try:
        w = WorkspaceClient()
        return w.config.host
    except Exception as e:
        logging.exception(f"Error getting databricks host from env: {e}")
        return None


def build_a2a_message(request: ResponsesAgentRequest) -> dict[str, Any]:
    chat_messages = to_chat_completions_input([item.model_dump() for item in request.input])
    if not chat_messages:
        raise ValueError("ResponsesAgentRequest.input must contain at least one message")

    last_message = chat_messages[-1]
    metadata: dict[str, Any] = {"history_length": len(request.input)}
    if request.custom_inputs:
        metadata["custom_inputs"] = request.custom_inputs
    if request.context:
        metadata["context"] = request.context.model_dump(exclude_none=True)

    message: dict[str, Any] = {
        "messageId": str(uuid4()),
        "role": last_message.get("role", "user"),
        "parts": _content_to_a2a_parts(last_message.get("content")),
        "metadata": metadata,
    }
    if context_id := get_session_id(request):
        message["contextId"] = context_id
    return message


def _content_to_a2a_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"kind": "text", "text": content}]

    parts: list[dict[str, Any]] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                parts.append({"kind": "text", "text": item})
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append({"kind": "text", "text": text})
                else:
                    parts.append({"kind": "data", "data": item})

    if not parts:
        parts.append({"kind": "text", "text": json.dumps(content, sort_keys=True)})
    return parts


async def process_a2a_stream_events(
    async_stream: AsyncIterator[dict[str, Any]],
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    item_id = str(uuid4())
    last_payload: dict[str, Any] | None = None
    aggregated_text = ""

    async for payload in async_stream:
        last_payload = payload
        current_text = extract_text_from_a2a_payload(payload)
        if not current_text:
            continue

        if current_text.startswith(aggregated_text):
            delta = current_text[len(aggregated_text) :]
            aggregated_text = current_text
        elif aggregated_text and current_text in aggregated_text:
            delta = ""
        else:
            delta = current_text if not aggregated_text else current_text
            aggregated_text = aggregated_text + delta if aggregated_text else current_text

        if delta:
            yield ResponsesAgentStreamEvent(**create_text_delta(delta=delta, item_id=item_id))

    if aggregated_text:
        yield ResponsesAgentStreamEvent(
            type="response.output_item.done",
            item=create_text_output_item(aggregated_text, item_id),
        )
    elif last_payload is not None:
        fallback_text = json.dumps(last_payload, sort_keys=True)
        yield ResponsesAgentStreamEvent(
            type="response.output_item.done",
            item=create_text_output_item(fallback_text, item_id),
        )


def a2a_payload_to_response_output(payload: dict[str, Any]) -> list[dict[str, Any]]:
    item_id = str(uuid4())
    text = extract_text_from_a2a_payload(payload)
    if not text:
        text = json.dumps(payload, sort_keys=True)
    return [create_text_output_item(text, item_id)]


def extract_text_from_a2a_payload(payload: dict[str, Any]) -> str:
    fragments: list[str] = []
    _collect_text_fragments(payload, fragments)
    return "".join(fragments)


def _collect_text_fragments(value: Any, fragments: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_text_fragments(item, fragments)
        return

    if not isinstance(value, dict):
        return

    if value.get("kind") == "text" and isinstance(value.get("text"), str):
        fragments.append(value["text"])
        return

    if value.get("type") in {"text", "output_text", "input_text"} and isinstance(
        value.get("text"), str
    ):
        fragments.append(value["text"])
        return

    content = value.get("content")
    if isinstance(content, str):
        fragments.append(content)
    elif isinstance(content, list):
        _collect_text_fragments(content, fragments)

    for key in ("message", "messages", "parts", "artifact", "artifacts", "result", "status"):
        nested = value.get(key)
        if nested is not None:
            _collect_text_fragments(nested, fragments)
