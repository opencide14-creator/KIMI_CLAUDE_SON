"""AI Gateway Router — LiteLLM-compatible endpoint with model routing.

Runs an embedded FastAPI server that:
  1. Accepts Claude / OpenAI API requests
  2. Rewrites the model name and Authorization header
  3. Forwards to the configured backend (Kimi, Ollama, etc.)
  4. Streams the response back to the client

No subprocess LiteLLM — we implement the routing logic directly.
"""
from __future__ import annotations
import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime
from typing import AsyncIterator, Callable, Dict, List, Optional, TypeVar

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

from PyQt6.QtCore import QObject, pyqtSignal

from src.constants import ServiceStatus, ModelProvider, PROVIDER_URLS, DEFAULT_GATEWAY_PORT
from src.models.gateway import GatewayRoute, GatewayModel
from src.core.security import SECURITY, mask_sensitive, is_valid_key_format, validate_url_length
from src.models.state import get_state, SK
from src.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    get_circuit_breaker,
    get_all_circuit_breakers,
)
from src.utils.sanitization import Sanitizer
from src.utils.shutdown import ShutdownProtocol
from src.core.gateway.constitutional import ConstitutionalCore
from src.core.gateway.heartbeat_enforcer import GatewayHeartbeatEnforcer
from src.core.intel.master_log import MasterLog

log = logging.getLogger(__name__)


async def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0
) -> T:
    """Retry function with exponential backoff.

    Handles transient failures (network errors, rate limits, service unavailable)
    by retrying with exponential backoff: 1s, 2s, 4s, 8s...

    Args:
        func: Synchronous or async callable to retry
        max_retries: Maximum number of retry attempts (default 3)
        base_delay: Initial delay in seconds (default 1.0)
        max_delay: Maximum delay cap in seconds (default 30.0)

    Returns:
        The result of func() on success

    Raises:
        The last exception if all retries are exhausted
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func()
            else:
                return func()
        except Exception as e:
            last_exception = e

            is_transient = (
                isinstance(e, httpx.HTTPError) or
                "rate" in str(e).lower() or
                "429" in str(e) or
                "503" in str(e) or
                "timeout" in str(e).lower() or
                "connection" in str(e).lower()
            )

            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                log.warning(
                    "Attempt %d/%d failed (%s): %s. Retrying in %.1fs...",
                    attempt + 1, max_retries,
                    "transient" if is_transient else "permanent",
                    e, delay
                )
                await asyncio.sleep(delay)
            else:
                log.error("All %d retry attempts exhausted for %s", max_retries, func)

    raise last_exception


# EDGE-C1: Max stream buffer to prevent memory exhaustion
MAX_STREAM_BUFFER = 1024 * 1024  # 1MB max buffer

# SEC-C3: Valid API key prefixes by provider for validation
VALID_KEY_PREFIXES = {
    "sk-ant-": "Anthropic",
    "sk-": "OpenAI/Generic",
    "AIza": "Google",
}


class GatewayRouter(QObject):
    """Embedded FastAPI AI gateway with dynamic route management."""

    status_changed   = pyqtSignal(object)    # ServiceStatus
    request_routed   = pyqtSignal(str, str, str)  # route_name, source_model, target_model
    error_occurred   = pyqtSignal(str)

    def __init__(self, port: int = DEFAULT_GATEWAY_PORT):
        super().__init__()
        self._port     = self._validate_port(port)
        self._routes:  List[GatewayRoute] = []
        self._models:  List[GatewayModel] = []
        self._status   = ServiceStatus.STOPPED
        self._server:  Optional[uvicorn.Server] = None
        self._thread:  Optional[threading.Thread] = None
        self._app      = self._build_app()
        self._http     = httpx.AsyncClient(timeout=120.0, follow_redirects=True)
        # Circuit breakers per backend provider — keyed by provider name
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        # Constitutional injection + MasterLog + Heartbeat
        self._constitutional = ConstitutionalCore()
        self._master_log = MasterLog()
        self._heartbeat_enforcer = GatewayHeartbeatEnforcer()
        # S-13: Load persisted routes on boot
        self._load_routes()

    # ── Route persistence (S-13 fix) ──────────────────────────────

    def _save_routes(self):
        """Persist routes to ROUTES_FILE (~/.sovereign/routes.json)."""
        from src.constants import ROUTES_FILE
        try:
            ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "id":              r.id,
                    "name":            r.name,
                    "source_host":     r.source_host,
                    "target_url":      r.target_url,
                    "target_provider": r.target_provider.value if hasattr(r.target_provider, "value") else str(r.target_provider),
                    "target_model":    r.target_model,
                    "inject_key":      r.inject_key,
                    "enabled":         r.enabled,
                }
                for r in self._routes
            ]
            ROUTES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("Could not save gateway routes: %s", e, exc_info=True)

    def _load_routes(self):
        """Load persisted routes from ROUTES_FILE on startup."""
        from src.constants import ROUTES_FILE
        from src.constants import ModelProvider
        if not ROUTES_FILE.exists():
            return
        try:
            data = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
            for r in data:
                try:
                    provider = ModelProvider(r.get("target_provider", "custom"))
                except ValueError:
                    provider = ModelProvider.CUSTOM
                route = GatewayRoute(
                    id             = r.get("id", ""),
                    name           = r.get("name", ""),
                    source_host    = r.get("source_host", ""),
                    target_url     = r.get("target_url", ""),
                    target_provider= provider,
                    target_model   = r.get("target_model", ""),
                    inject_key     = r.get("inject_key", ""),
                    enabled        = r.get("enabled", True),
                )
                self._routes.append(route)
            get_state().set(SK.GATEWAY_ROUTES, list(self._routes))
            log.info("Gateway: loaded %d persisted routes", len(self._routes))
        except Exception as e:
            log.warning("Could not load gateway routes: %s", e, exc_info=True)

    @property
    def status(self) -> ServiceStatus:
        return self._status

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int):
        if self._status == ServiceStatus.STOPPED:
            self._port = self._validate_port(value)
        # else: ignored — cannot change port while running

    @staticmethod
    def _validate_port(port: int) -> int:
        """Validate and constrain port number.

        Args:
            port: The port number to validate.

        Returns:
            The validated port number.

        Raises:
            TypeError: If port is not an integer.
            ValueError: If port is < 1024, == 0, or > 65535.
        """
        if not isinstance(port, int):
            raise TypeError(f"Port must be int, got {type(port).__name__}")

        if port < 1024:
            raise ValueError(f"Port {port} requires root privileges")

        if port == 0:
            raise ValueError("Port 0 (dynamic allocation) not supported")

        if port > 65535:
            raise ValueError(f"Port {port} out of range (max 65535)")

        return port

    # ── Routes management ──────────────────────────────────────────

    def add_route(self, route: GatewayRoute):
        self._routes = [r for r in self._routes if r.id != route.id]
        self._routes.append(route)
        get_state().set(SK.GATEWAY_ROUTES, list(self._routes))
        self._save_routes()
        log.info("Gateway route added: %s", route.summary)

    def remove_route(self, route_id: str):
        self._routes = [r for r in self._routes if r.id != route_id]
        get_state().set(SK.GATEWAY_ROUTES, list(self._routes))
        self._save_routes()

    def add_model(self, model: GatewayModel):
        self._models = [m for m in self._models if m.id != model.id]
        self._models.append(model)
        get_state().set(SK.GATEWAY_MODELS, list(self._models))

    def remove_model(self, model_id: str):
        self._models = [m for m in self._models if m.id != model_id]
        get_state().set(SK.GATEWAY_MODELS, list(self._models))

    def _find_route_for_model(self, model_name: str) -> Optional[tuple[GatewayRoute, GatewayModel]]:
        """Find enabled route + model for the incoming model name.

        Matching priority:
          1. model.alias == model_name (exact)
          2. model_name contains 'claude' and route.source_host == 'api.anthropic.com'
          3. Any route with inject_key set (catch-all gateway redirect)
        """
        for route in self._routes:
            if not route.enabled:
                continue
            for model in self._models:
                if not model.enabled:
                    continue
                # Exact alias match
                if model.alias == model_name:
                    return route, model
                # Source host pattern match (e.g. claude-* → anthropic route)
                if route.source_host and route.source_host.split(".")[0] in model_name.lower():
                    return route, model

        # Fallback: if there is a route with inject_key and no model defined,
        # use a synthetic model built from the route itself
        for route in self._routes:
            if route.enabled and route.inject_key:
                from src.constants import PROVIDER_URLS
                synthetic = GatewayModel(
                    alias       = model_name,
                    provider    = route.target_provider,
                    real_model  = route.target_model or model_name,
                    base_url    = route.target_url,
                )
                return route, synthetic

        return None

    # ── FastAPI app ────────────────────────────────────────────────

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="SOVEREIGN AI Gateway", version="1.0.0")

        @app.get("/health")
        async def health():
            cb_statuses = {
                name: cb.state.value
                for name, cb in self._circuit_breakers.items()
            }
            return {
                "status": "ok",
                "routes": len(self._routes),
                "models": len(self._models),
                "circuit_breakers": cb_statuses,
            }

        @app.get("/v1/models")
        async def list_models():
            data = [
                {"id": m.alias, "object": "model", "owned_by": m.provider.value}
                for m in self._models if m.enabled
            ]
            return {"object": "list", "data": data}

        # ── Messages endpoint (Anthropic-style) ────────────────────
        @app.post("/v1/messages")
        async def messages(request: Request):
            return await self._handle_messages(request)

        # ── Chat completions endpoint (OpenAI-style) ──────────────
        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            return await self._handle_chat_completions(request)

        # ── Kimi coding endpoint ───────────────────────────────────
        @app.post("/coding/v1/chat/completions")
        async def kimi_chat_completions(request: Request):
            return await self._handle_chat_completions(request)

        @app.api_route("/{full_path:path}", methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
        async def catch_all(full_path: str, request: Request):
            return await self._handle_passthrough(full_path, request)

        return app

    # ── Request handlers ───────────────────────────────────────────

    async def _handle_messages(self, request: Request) -> StreamingResponse | JSONResponse:
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from e

        model_name = body.get("model", "")
        match = self._find_route_for_model(model_name)

        if not match:
            # No route — forward to real Anthropic
            return await self._passthrough_request(
                "https://api.anthropic.com/v1/messages",
                request, body
            )

        route, model = match

        # CONSTITUTIONAL INJECTION (Phase 5)
        session_id = request.headers.get("x-opencode-session-id", "unknown")
        body, inject_log = self._constitutional.inject(body, session_id)
        self._master_log.write("constitutional_inject", inject_log, session_id=session_id)

        # BUG-19 FIX: Translate Anthropic format to OpenAI format
        # Anthropic uses content blocks [{"type":"text","text":"..."}], OpenAI uses plain string
        openai_body = self._translate_anthropic_to_openai(body, model)

        return await self._forward_request(route, model, request, openai_body, "/v1/chat/completions")

    def _translate_anthropic_to_openai(self, body: dict, model: GatewayModel) -> dict:
        """Translate Anthropic /v1/messages format to OpenAI /v1/chat/completions format."""
        messages = body.get("messages", [])
        translated_messages = []

        for msg in messages:
            translated_msg = dict(msg)
            content = msg.get("content")

            if isinstance(content, list):
                # Anthropic content blocks: [{"type": "text", "text": "..."}]
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                translated_msg["content"] = "\n".join(text_parts)
            elif content is None:
                translated_msg["content"] = ""
            # else: content is already a string, keep as-is

            translated_messages.append(translated_msg)

        return {
            "model": model.real_model,
            "messages": translated_messages,
            "stream": body.get("stream", False),
            "max_tokens": body.get("max_tokens"),
            "temperature": body.get("temperature"),
            "top_p": body.get("top_p"),
            "stop": body.get("stop"),
        }

    def _translate_openai_to_anthropic(self, openai_response: dict) -> dict:
        """Complete OpenAI response to Anthropic format."""
        choice = openai_response.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Generate required Anthropic fields
        return {
            "id": f"msg_{uuid.uuid4().hex[:8]}",
            "type": "message",
            "role": message.get("role", "assistant"),
            "content": [
                {
                    "type": "text",
                    "text": message.get("content", "")
                }
            ],
            "model": openai_response.get("model", "unknown"),
            "stop_reason": choice.get("finish_reason", "end_turn"),
            "stop_sequence": None,
            "usage": {
                "input_tokens": openai_response.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": openai_response.get("usage", {}).get("completion_tokens", 0)
            }
        }

    async def _handle_chat_completions(self, request: Request) -> StreamingResponse | JSONResponse:
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from e

        model_name = body.get("model", "")
        match = self._find_route_for_model(model_name)

        if not match:
            return await self._passthrough_request(
                "https://api.openai.com/v1/chat/completions",
                request, body
            )

        route, model = match

        # CONSTITUTIONAL INJECTION (Phase 5)
        session_id = request.headers.get("x-opencode-session-id", "unknown")
        body, inject_log = self._constitutional.inject(body, session_id)
        self._master_log.write("constitutional_inject", inject_log, session_id=session_id)

        rewritten = dict(body)
        rewritten["model"] = model.real_model
        return await self._forward_request(route, model, request, rewritten, "/v1/chat/completions")

    async def _handle_passthrough(self, path: str, request: Request):
        raise HTTPException(status_code=404, detail=f"Route not configured for /{path}")

    def _get_circuit_breaker(self, model: GatewayModel) -> CircuitBreaker:
        """Get or create a circuit breaker for a model's provider."""
        provider = model.provider.value if hasattr(model.provider, "value") else str(model.provider)
        if provider not in self._circuit_breakers:
            self._circuit_breakers[provider] = CircuitBreaker(
                name=f"{provider}",
                failure_threshold=5,
                recovery_timeout=30.0,
            )
        return self._circuit_breakers[provider]

    async def _forward_request(
        self, route: GatewayRoute, model: GatewayModel,
        original_request: Request, body: dict, endpoint: str
    ) -> StreamingResponse | JSONResponse:
        target_url = (model.base_url or route.target_url).rstrip("/") + endpoint
        headers    = self._build_headers(original_request, route, model)

        route.request_count += 1
        route.last_used = datetime.now()
        count = get_state().get(SK.GATEWAY_REQUEST_COUNT, 0) + 1
        get_state().set(SK.GATEWAY_REQUEST_COUNT, count)
        self.request_routed.emit(route.name, body.get("model","?"), model.real_model)

        stream = body.get("stream", False)
        log.info("Routing %s → %s [%s]", body.get("model"), model.real_model, target_url)

        # Get circuit breaker for this backend
        cb = self._get_circuit_breaker(model)

        try:
            if stream:
                # Determine if target is Anthropic (pass-through) or needs translation
                target_is_anthropic = original_request.url.path != "/v1/messages"
                return StreamingResponse(
                    self._stream_forward(cb, target_url, headers, body, target_is_anthropic),
                    media_type="text/event-stream",
                )
            else:
                # Wrap the HTTP call with the circuit breaker
                async def _forward_non_stream():
                    return await self._http.post(target_url, json=body, headers=headers)

                resp = await cb.call_async(_forward_non_stream)
                # BUG-19 FIX: Translate OpenAI response back to Anthropic format
                if original_request.url.path == "/v1/messages":
                    anthropic_content = self._translate_openai_to_anthropic(resp.json())
                    return JSONResponse(content=anthropic_content, status_code=resp.status_code)
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except CircuitOpenError as e:
            route.error_count += 1
            retry_msg = f" (retry after {e.retry_after:.1f}s)" if e.retry_after else ""
            detail = f"Circuit breaker open for {model.provider.value if hasattr(model.provider, 'value') else model.provider}: {e}{retry_msg}"
            log.warning("Circuit breaker open: %s", detail)
            self.error_occurred.emit(detail)
            raise HTTPException(status_code=503, detail=detail) from e
        except httpx.ConnectError as e:
            route.error_count += 1
            self.error_occurred.emit(f"Connection refused to {target_url}: {e}")
            raise HTTPException(status_code=502, detail=f"Cannot reach backend: {target_url}") from e
        except Exception as e:
            route.error_count += 1
            self.error_occurred.emit(str(e))
            raise HTTPException(status_code=500, detail=str(e)) from e

    async def _stream_forward(
        self,
        cb: CircuitBreaker,
        url: str,
        headers: dict,
        body: dict,
        target_is_anthropic: bool = False,
    ) -> AsyncIterator[bytes]:
        """Forward streaming request and optionally translate OpenAI SSE to Anthropic format."""
        try:
            async with self._http.stream("POST", url, json=body, headers=headers) as resp:
                if target_is_anthropic:
                    # Pass through Anthropic streaming response directly
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                else:
                    # Translate OpenAI SSE chunks to Anthropic format
                    async for chunk in self._translate_openai_stream_to_anthropic(resp.aiter_bytes()):
                        yield chunk
        except Exception as e:
            log.error("Stream forward error: %s", e, exc_info=True)
            yield f'data: {json.dumps({"error": str(e)})}\n\n'.encode()

    async def _translate_openai_stream_to_anthropic(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Translate OpenAI streaming response chunks to Anthropic SSE format.

        EDGE-C1 Fix: Implements buffer size tracking to prevent memory exhaustion
        when stream lacks newlines and buffer grows unbounded.
        """
        buffer = b""
        total_size = 0
        truncated = False

        async for chunk in chunks:
            # EDGE-C1: Check buffer size to prevent exhaustion
            if total_size + len(chunk) > MAX_STREAM_BUFFER:
                log.warning("Stream buffer exceeded %d bytes, truncating", MAX_STREAM_BUFFER)
                yield b"data: {\"type\": \"content_block_delta\", \"index\": 0, \"delta\": {\"type\": \"text_delta\", \"text\": \"[... response truncated due to size ...]\"}}\n\n"
                truncated = True
                break

            buffer += chunk
            total_size += len(chunk)
            lines = buffer.split(b"\n")
            buffer = lines[-1]  # Keep incomplete line in buffer

            for line in lines[:-1]:
                line = line.strip()
                if not line or not line.startswith(b"data: "):
                    continue

                data = line[6:]  # Remove "data: " prefix
                if data == b"[DONE]":
                    yield b"data: {\"type\": \"message_stop\"}\n\n"
                    continue

                try:
                    parsed = json.loads(data)
                    # Translate OpenAI chunk to Anthropic SSE
                    yield self._translate_openai_chunk_to_anthropic(parsed).encode()
                except json.JSONDecodeError:
                    continue

        # Handle remaining buffer (only if not truncated)
        if not truncated and buffer.strip() and buffer.startswith(b"data: "):
            data = buffer[6:]
            if data != b"[DONE]":
                try:
                    parsed = json.loads(data)
                    yield self._translate_openai_chunk_to_anthropic(parsed).encode()
                except json.JSONDecodeError:
                    pass

    def _translate_openai_chunk_to_anthropic(self, chunk: dict) -> str:
        """Translate streaming chunk to Anthropic SSE format."""
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        content = delta.get("content", "")
        finish = chunk.get("choices", [{}])[0].get("finish_reason")

        if content:
            return f'data: {{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{content}"}}}}\n\n'

        if finish:
            return 'data: {"type":"message_stop","index":0}\n\n'

        return ""

    async def _passthrough_request(
        self, url: str, request: Request, body: dict
    ) -> JSONResponse:
        headers = dict(request.headers)
        headers.pop("host", None)
        try:
            resp = await self._http.post(url, json=body, headers=headers)
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except httpx.ConnectError as e:
            raise HTTPException(status_code=502, detail=f"Cannot reach backend: {url}") from e
        except httpx.TimeoutException as e:
            raise HTTPException(status_code=504, detail=f"Backend timeout: {url}") from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    def _build_headers(self, request: Request, route: GatewayRoute, model: GatewayModel) -> dict:
        headers = {"Content-Type": "application/json"}

        # Determine the API key to use
        key = None
        if route.inject_key:
            key = route.inject_key
        elif model.api_key_vault_ref:
            from src.core.vault.store import VaultStore
            key = VaultStore.get_key(model.api_key_vault_ref)

        # SEC-C3: Validate key format before injection
        if key:
            provider_value = model.provider.value if hasattr(model.provider, 'value') else str(model.provider)
            if not self._validate_api_key(key, provider_value):
                log.warning(
                    "SEC-C3: Suspicious API key format for provider %s (route: %s, key: %s)",
                    provider_value,
                    route.name,
                    self._sanitize_key_for_logging(key)
                )
                raise HTTPException(400, "Invalid API key format")

        # Apply key with provider-specific header format
        if key:
            if model.provider == ModelProvider.GEMINI:
                headers["x-goog-api-key"] = key
            else:
                headers["Authorization"] = f"Bearer {key}"

        # Add any extra headers from route
        for k, v in route.extra_headers.items():
            headers[k] = v
        return headers

    # SEC-C3: API key validation ───────────────────────────────────

    def _validate_api_key(self, key: str, provider: str) -> bool:
        """Validate that API key format matches provider using centralized security config.

        Returns True if key format is valid for the given provider,
        False otherwise. Unknown providers are allowed but logged.
        """
        return is_valid_key_format(key, provider)

    def _sanitize_key_for_logging(self, key: str) -> str:
        """Sanitize API key for safe logging using centralized security config."""
        return mask_sensitive(key)

    # ── Service lifecycle ──────────────────────────────────────────

    def start(self):
        if self._status == ServiceStatus.RUNNING:
            return
        self._set_status(ServiceStatus.STARTING)
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.should_exit = True
        self._set_status(ServiceStatus.STOPPED)

    async def stop_async(self):
        """Async graceful shutdown of GatewayRouter."""
        await ShutdownProtocol("GatewayRouter", timeout=10.0).shutdown(
            self._server,
            self._http,
            self._thread,
        )
        self._server = None
        self._set_status(ServiceStatus.STOPPED)

    def _safe_close_http(self):
        """Safely close httpx client, ignoring event-loop-closed errors."""
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            return  # no event loop → nothing to close
        try:
            if self._http and not self._http.is_closed:
                asyncio.get_event_loop().run_until_complete(self._http.aclose())
        except RuntimeError:
            pass  # event loop closed during teardown

    def _run_server(self):
        try:
            config = uvicorn.Config(
                self._app,
                host="127.0.0.1",
                port=self._port,
                log_level="warning",
                access_log=False,
            )
            self._server = uvicorn.Server(config)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._set_status(ServiceStatus.RUNNING)
            log.info("Gateway running on 127.0.0.1:%d", self._port)
            loop.run_until_complete(self._server.serve())
        except Exception as e:
            log.error("Gateway error: %s", e, exc_info=True)
            self._set_status(ServiceStatus.ERROR)
            self.error_occurred.emit(str(e))

    def _set_status(self, status: ServiceStatus):
        self._status = status
        self.status_changed.emit(status)
        get_state().set(SK.GATEWAY_STATUS, status)
