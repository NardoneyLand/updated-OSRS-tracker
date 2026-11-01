"""OpenRouter / OpenAI-compatible path: fake server first, then free checks against real OpenRouter."""
import json, os, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_llm as F
port = F.start()
FAKE = f"http://127.0.0.1:{port}/v1"
import ai, aitools, settings
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "s.json")
GO = threading.Event


class StubTools:
    def __init__(self):
        self.calls = []

    def run(self, name, args):
        self.calls.append((name, args))
        if not isinstance(args, dict):
            return {"error": "Tool arguments must be an object."}
        return {"ok": name, "args": args}


def chunk(delta=None, finish=None, usage=None, **top):
    d = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
         "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if delta is not None or finish else []}
    if usage:
        d["usage"] = usage
    d.update(top)
    return {"type": "chunk", **d}


def text(t, finish=None):
    return chunk({"role": "assistant", "content": t}, finish)


def tc(index, id_=None, name=None, args="", **extra):
    fn = {"arguments": args}
    if name:
        fn["name"] = name
    part = {"function": fn, **extra}
    if index is not None:
        part["index"] = index
    if id_:
        part["id"] = id_
        part["type"] = "function"
    return part


def run(url=FAKE, provider=ai.COMPATIBLE, model="m", q="q", cancel=None, chat=None):
    tools = StubTools()
    chat = chat or ai.Chat(provider, model, tools, url)
    out = []
    res = chat.send(q, "k" * 20, out.append, lambda s: None, cancel or GO())
    return chat, "".join(out), res, chat.tools


def expect(fragment, *scripts):
    F.SCRIPTS["compat"] = list(scripts)
    try:
        run()
    except ai.AIError as e:
        assert fragment in str(e), str(e)
        return str(e)
    raise AssertionError("expected AIError: " + fragment)


# Tool call split across chunks, with reasoning in all the non-standard fields
F.SCRIPTS["compat"] = [
    [chunk({"role": "assistant", "reasoning": "Think ", "reasoning_details": [{"type": "reasoning.text", "text": "Think ", "index": 0}]}),
     chunk({"reasoning": "more", "reasoning_details": [{"type": "reasoning.encrypted", "data": "ENC", "index": 1}]}),
     chunk({"content": "Checking. "}),
     chunk({"tool_calls": [tc(0, "call_a", "get_item", '{"na')]}),
     chunk({"tool_calls": [tc(0, None, None, 'me":"whip"}')]}),
     chunk({"tool_calls": [tc(1, "call_b", "get_news", "{}", extra_content={"google": {"thought_signature": "SIG"}})]}),
     chunk(finish="tool_calls"),
     chunk(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})],
    [text("Buy it."), chunk(finish="stop", usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25})],
]
chat, out, res, tools = run()
assert out == "Checking. Buy it." and res == {"tokens": 40, "notice": ""}, (out, res)
assert tools.calls == [("get_item", {"name": "whip"}), ("get_news", {})], tools.calls
body = F.REQUESTS[-1]["body"]
assert F.REQUESTS[-1]["path"] == "/v1/chat/completions"
assert body["messages"][0]["role"] == "system" and body["messages"][1] == {"role": "user", "content": "q"}
a = body["messages"][2]
print("assistant echo:", json.dumps(a)[:520])
assert a["role"] == "assistant" and a["content"] == "Checking. " and a["reasoning"] == "Think more"
assert [d["index"] for d in a["reasoning_details"]] == [0, 1] and a["reasoning_details"][1]["data"] == "ENC"
assert a["tool_calls"][0] == {"id": "call_a", "type": "function", "function": {"name": "get_item", "arguments": '{"name":"whip"}'}}
assert a["tool_calls"][1]["extra_content"] == {"google": {"thought_signature": "SIG"}}, a["tool_calls"][1]
assert body["messages"][3]["role"] == "tool" and body["messages"][3]["tool_call_id"] == "call_a"
assert body["messages"][4]["tool_call_id"] == "call_b"
assert body["tools"][0]["function"]["name"] == "get_flips" and body["max_tokens"] == ai.COMPATIBLE_MAX_TOKENS
assert "stream_options" not in body and "reasoning" not in body and body["tool_choice"] == "auto"
assert [m["role"] for m in chat.history] == ["user", "assistant", "tool", "tool", "assistant"]
assert all(m["role"] != "system" for m in chat.history), "system prompt must not pile up in history"

# Follow-up resends history unchanged, with exactly one system prompt, and tools again
F.SCRIPTS["compat"] = [[text("ok", "stop")]]
run(chat=chat, q="next")
after = F.REQUESTS[-1]["body"]
assert sum(m["role"] == "system" for m in after["messages"]) == 1 and after["tools"]
assert json.dumps(after["messages"][1:5]) == json.dumps(body["messages"][1:5])

# Services that number every call 0 and tell them apart by id; and ones with no id or index
F.SCRIPTS["compat"] = [
    [chunk({"tool_calls": [tc(0, "x1", "get_news", "{}")]}), chunk({"tool_calls": [tc(0, "x2", "get_movers", "{}")]}), chunk(finish="tool_calls")],
    [text("done", "stop")]]
_, _, _, t = run()
assert [c[0] for c in t.calls] == ["get_news", "get_movers"], t.calls
F.SCRIPTS["compat"] = [[chunk({"tool_calls": [tc(None, None, "get_news", "{}")]}), chunk(finish="stop")], [text("done", "stop")]]
_, out, _, t = run()
assert t.calls == [("get_news", {})] and F.REQUESTS[-2]["body"]["messages"][-1]["role"] == "user"
assert F.REQUESTS[-1]["body"]["messages"][-1]["tool_call_id"] == "call_0"

# Bad JSON args go back to the model as an error, not a crash
F.SCRIPTS["compat"] = [[chunk({"tool_calls": [tc(0, "b", "get_item", '{"na')]}), chunk(finish="tool_calls")], [text("sorry", "stop")]]
_, _, _, t = run()
assert "error" in json.loads(F.REQUESTS[-1]["body"]["messages"][-1]["content"])

# Errors and odd endings
expect("could not finish", [chunk(finish="error")])
msg = expect("Provider disconnected", [text("par"), chunk(finish="error", error={"code": "server_error", "message": "Provider disconnected unexpectedly"})])
print("mid-stream error ->", msg)
expect("empty reply", [chunk(finish="stop")])
expect("cut off", [chunk({"tool_calls": [tc(0, "c", "get_news", "{")]}), chunk(finish="length")])
F.SCRIPTS["compat"] = [[text("long"), chunk(finish="length")]]
assert run()[2]["notice"] == ai.NOTICE_CUT_OFF
F.SCRIPTS["compat"] = [[text("no"), chunk(finish="content_filter")]]
assert "content filter" in run()[2]["notice"]
err = lambda code, m: {"status": code, "json": {"error": {"code": code, "message": m}}}
msg = expect("support tools", err(404, "No endpoints found that support tool use."))
print("no-tools ->", msg)
print("402 ->", expect("out of credit", err(402, "This request requires more credits, or fewer max_tokens.")))
print("401 ->", expect("127.0.0.1 rejected", err(401, "User not found.")))
expect("recognise", err(404, "model not found"))

# max_tokens above the model's limit: retried once without it
F.SCRIPTS["compat"] = [err(400, "max_tokens must be less than or equal to 4096"), [text("fine", "stop")]]
_, out, _, _ = run()
assert out == "fine" and "max_tokens" not in F.REQUESTS[-1]["body"]

# Tool round cap forbids tools on the last request
F.SCRIPTS["compat"] = [[chunk({"tool_calls": [tc(0, f"r{i}", "get_news", "{}")]}), chunk(finish="tool_calls")] for i in range(ai.MAX_TOOL_ROUNDS)] + [[text("end", "stop")]]
assert run()[2]["notice"] == ai.NOTICE_TOOL_LIMIT and F.REQUESTS[-1]["body"]["tool_choice"] == "none"

# Cancel commits nothing
F.SCRIPTS["compat"] = [[text("abc", "stop")]]
stop = GO()
stop.set()
assert run(cancel=stop)[0].history == []

# URL rules
c = ai.clean_base_url
assert c(" https://api.groq.com/openai/v1/ ") == "https://api.groq.com/openai/v1"
assert c("https://x.ai/v1/chat/completions") == "https://x.ai/v1"
for ok in ("http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://192.168.1.5:8000/v1"):
    assert c(ok) == ok
for bad in ("http://api.example.com/v1", "api.example.com/v1", "", "ftp://x/v1", "https://", "http://[bad/v1"):
    try:
        c(bad)
        raise AssertionError("accepted " + bad)
    except ai.AIError as e:
        pass
assert ai.base_url(ai.ANTHROPIC) is None and ai.base_url(ai.OPENROUTER) == ai.OPENROUTER_URL
assert ai.base_url(ai.COMPATIBLE) is None
settings.save({"ai_base_url": "https://api.groq.com/openai/v1"})
assert ai.base_url(ai.COMPATIBLE) == "https://api.groq.com/openai/v1"
assert ai.service_name(ai.COMPATIBLE, ai.base_url(ai.COMPATIBLE)) == "api.groq.com"
assert ai.service_name(ai.OPENROUTER, ai.OPENROUTER_URL) == "OpenRouter"

# Key checks on a compatible service: a 404 on /models doesn't block saving
assert ai.verify_key(ai.COMPATIBLE, "good-key-000", FAKE) is None
try:
    ai.verify_key(ai.COMPATIBLE, "bad-key-000", FAKE)
    raise AssertionError("bad key accepted")
except ai.AIError as e:
    assert "127.0.0.1 rejected" in str(e)
print("no server ->", ai.verify_key(ai.COMPATIBLE, "good-key-000", "http://127.0.0.1:9/v1"))

# Model list drops tool-less and batch models when the service says so; keeps all when it doesn't say
F.MODELS_DATA[:] = [{"id": "b/no-tools", "object": "model", "supported_parameters": ["max_tokens"]},
                    {"id": "a/tools", "object": "model", "supported_parameters": ["tools"]},
                    {"id": "a/tools:batch", "object": "model", "supported_parameters": ["tools"]},
                    {"id": "c/unknown", "object": "model"}]
assert ai.list_models("good-key-000", FAKE) == ["a/tools", "c/unknown"], ai.list_models("good-key-000", FAKE)
print("fake server ok")

# ── Real OpenRouter, free checks only (a deliberately invalid key; no model runs) ──
if os.environ.get("OSRS_LIVE"):
    BAD = "sk-or-v1-deliberately-invalid-key"
    live = ai.list_models(BAD, ai.OPENROUTER_URL)
    print("real OpenRouter models with tools:", len(live))
    for m in ai.MODELS[ai.OPENROUTER]:
        assert m in live, "suggested model missing from OpenRouter: " + m
    assert not any(m.endswith(":batch") for m in live)
    try:
        ai.verify_key(ai.OPENROUTER, BAD, ai.OPENROUTER_URL)
        raise AssertionError("OpenRouter accepted a bad key")
    except ai.AIError as e:
        print("real bad key ->", e)
    try:
        ai.Chat(ai.OPENROUTER, ai.MODELS[ai.OPENROUTER][0], StubTools(), ai.OPENROUTER_URL).send(
            "hi", BAD, lambda t: None, lambda s: None, GO())
        raise AssertionError("OpenRouter chat accepted a bad key")
    except ai.AIError as e:
        print("real chat with bad key ->", e)
        assert "rejected" in str(e)
print("ALL PASSED")
