from typing import AsyncGenerator

import mlflow
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.a2a_client import FoundryA2AClient
from agent_server.utils import (
    a2a_payload_to_response_output,
    build_a2a_message,
    get_session_id,
    process_a2a_stream_events,
)
_a2a_client = FoundryA2AClient.from_env()


def init_agent() -> FoundryA2AClient:
    return _a2a_client


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    session_id = get_session_id(request)
    if session_id:
        mlflow.update_current_trace(
            metadata={
                "mlflow.trace.session": session_id,
                "foundry.a2a.agent": init_agent().agent_name,
            }
        )
    payload = await init_agent().send_message(
        message=build_a2a_message(request),
        context_id=session_id,
    )
    return ResponsesAgentResponse(output=a2a_payload_to_response_output(payload))


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    session_id = get_session_id(request)
    if session_id:
        mlflow.update_current_trace(
            metadata={
                "mlflow.trace.session": session_id,
                "foundry.a2a.agent": init_agent().agent_name,
            }
        )

    message = build_a2a_message(request)
    client = init_agent()

    async for event in process_a2a_stream_events(
        client.stream_message(message=message, context_id=session_id),
    ):
        yield event
