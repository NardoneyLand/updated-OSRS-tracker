"""Local stand-in for both provider APIs: replays scripted SSE streams and records requests."""
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUESTS = []
SCRIPTS = {"anthropic": [], "openai": [], "compat": []}
MODELS_DATA = []


def sse(events):
    out = b""
    for e in events:
        if e.get("type") == "chunk":
            e = {k: v for k, v in e.items() if k != "type"}
            out += f": OPENROUTER PROCESSING\n\ndata: {json.dumps(e)}\n\n".encode()
        else:
            out += f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()
    if events and "object" in events[0] or (events and events[0].get("type") == "chunk"):
        out += b"data: [DONE]\n\n"
    return out


def msg_start(model="claude-opus-5-5"):
    return {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
        "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0}}}


def msg_end(reason):
    return [{"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None}, "usage": {"output_tokens": 20}},
            {"type": "message_stop"}]


def a_text(i, text):
    return [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": "", "citations": None}},
            {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": text}},
            {"type": "content_block_stop", "index": i}]


def a_tool(i, id_, name, args):
    return [{"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": id_, "name": name, "input": {}}},
            {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}},
            {"type": "content_block_stop", "index": i}]


def a_think(i):
    return [{"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": "SIG123"}},
            {"type": "content_block_stop", "index": i}]


def a_fallback(i):
    return [{"type": "content_block_start", "index": i, "content_block": {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}}},
            {"type": "content_block_stop", "index": i}]


def o_resp(status, output):
    return {"id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-6.1-sol", "status": status,
            "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
            "error": None, "instructions": None, "metadata": {}, "temperature": 1, "top_p": 1,
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 5}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        REQUESTS.append({"path": self.path, "headers": dict(self.headers), "body": None})
        key = self.headers.get("x-api-key") or self.headers.get("authorization", "").replace("Bearer ", "")
        code = 401 if "bad" in key else 200
        body = json.dumps({"data": MODELS_DATA, "object": "list", "has_more": False} if code == 200 else
                          {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        REQUESTS.append({"path": self.path, "headers": dict(self.headers), "body": body})
        which = ("anthropic" if "messages" in self.path
                 else "compat" if "chat/completions" in self.path else "openai")
        step = SCRIPTS[which].pop(0)
        if isinstance(step, dict):   # plain JSON error
            data = json.dumps(step["json"]).encode()
            self.send_response(step["status"])
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        data = sse(step)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]
