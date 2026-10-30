"""Small in-process MCP HTTP endpoint; all traffic stays on loopback."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TOOLS = [
    {
        "name": "first",
        "description": "First tool.",
        "inputSchema": {
            "type": "object",
            "title": "firstArguments",
            "properties": {"city": {"type": "string", "title": "City"}},
            "required": ["city"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {"name": "second", "inputSchema": {"type": "object"}},
]


class Endpoint:
    def __init__(self, *, sse: bool = False, mode: str = "ok", delay: str | None = None):
        self.sse = sse
        self.mode = mode
        self.delay = delay
        self.requests: list[dict[str, Any]] = []
        self.terminated = False
        self.url = ""


@contextlib.contextmanager
def serve_http(**kwargs: Any) -> Iterator[Endpoint]:
    endpoint = Endpoint(**kwargs)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            self.send_response(405)  # a separate notification stream is optional
            self.end_headers()

        def do_DELETE(self) -> None:
            endpoint.terminated = True
            self.send_response(204)
            self.end_headers()

        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            endpoint.requests.append(request)
            method = request["method"]
            if method == endpoint.delay:
                time.sleep(0.5)
            if "id" not in request:
                self.send_response(202)
                self.end_headers()
                return
            if endpoint.mode == "http_error":
                self.send_response(503)
                self.end_headers()
                return
            if endpoint.mode in ("html", "bad_json"):
                self.send_response(200)
                self.send_header(
                    "Content-Type", "text/html" if endpoint.mode == "html" else "application/json"
                )
                data = b"<html>Wrong endpoint</html>" if endpoint.mode == "html" else b"{invalid"
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                with contextlib.suppress(ConnectionError):
                    self.wfile.write(data)
                return
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "toolism-http-test", "version": "1"},
                }
            elif method == "tools/list":
                cursor = request.get("params", {}).get("cursor")
                if endpoint.mode == "empty":
                    result = {"tools": []}
                elif endpoint.mode == "repeat":
                    result = {"tools": TOOLS[:1], "nextCursor": "again"}
                elif endpoint.mode == "endless":
                    result = {"tools": [], "nextCursor": str(int(cursor or 0) + 1)}
                else:
                    result = (
                        {"tools": TOOLS[1:]}
                        if cursor == "page2"
                        else {"tools": TOOLS[:1], "nextCursor": "page2"}
                    )
            else:
                raise AssertionError(f"unexpected method: {method}")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            if endpoint.mode == "rpc_error" and method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32603, "message": "cannot list\ntry later"},
                }
            text = json.dumps(response)
            if endpoint.sse:
                text = f"event: message\ndata: {text}\n\n"
            data = text.encode()
            self.send_response(200)
            if method == "initialize":
                self.send_header("Mcp-Session-Id", "test-session")
            self.send_header(
                "Content-Type", "text/event-stream" if endpoint.sse else "application/json"
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            with contextlib.suppress(ConnectionError):
                self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint.url = f"http://127.0.0.1:{server.server_port}/mcp"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
