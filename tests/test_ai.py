import json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_llm as F
port = F.start()
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
import tempfile, db, settings
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), 'settings.json')
db.DB_NAME = os.path.join(tempfile.mkdtemp(), 't.db')
import api, ai, aitools

GO = threading.Event


def noop(_t):
    pass


def send(chat, q="q", cancel=None):
    return chat.send(q, "k" * 20, noop, noop, cancel or GO())


def expect_error(chat, fragment):
    try:
        send(chat)
    except ai.AIError as e:
        assert fragment in str(e), str(e)
        return
    raise AssertionError("expected AIError: " + fragment)


# Offline market data
now = time.time()
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True, "highalch": 72000, "examine": "A weapon."},
           {"id": 2, "name": "Lobster", "limit": 13000, "members": False, "highalch": 100},
           {"id": 3, "name": "Dead item", "limit": 5, "members": False}]
LATEST = {"1": {"high": 2_000_000, "low": 1_900_000, "highTime": now-30, "lowTime": now-40},
          "2": {"high": 200, "low": 180, "highTime": now-30, "lowTime": now-40},
          "3": {"high": 500, "low": 100, "highTime": now-9000, "lowTime": now-9000}}
BULK = {"1": {"avgHighPrice": 1_990_000, "avgLowPrice": 1_910_000, "highPriceVolume": 500, "lowPriceVolume": 400},
        "2": {"avgHighPrice": 185, "avgLowPrice": 175, "highPriceVolume": 50000, "lowPriceVolume": 40000},
        "3": {"avgHighPrice": 400, "avgLowPrice": 150, "highPriceVolume": 1, "lowPriceVolume": 1}}
TS = [{"timestamp": int(now) - 3600*(400-i), "avgHighPrice": 1_800_000+i*500, "avgLowPrice": 1_750_000+i*500,
       "highPriceVolume": 10, "lowPriceVolume": 9} for i in range(365)]
fetches = []
api.get_all_latest_prices = lambda: (fetches.append(1), LATEST)[1]
api.get_bulk_average = lambda t="1h": BULK
api.get_timeseries = lambda i, timestep="1h": TS
tools = aitools.MarketTools(lambda: MAPPING)

# ── tools ──
r = tools.run("get_flips", {"capital": "10m"})
assert [f["name"] for f in r["flips"]] == ["Abyssal whip", "Lobster"], r
assert r["capital_used_gp"] == 10_000_000 and r["matching_items"] == 2
assert tools.run("get_flips", {"verdict": "Inactive", "capital": "10m"})["flips"][0]["name"] == "Dead item"
assert tools.run("get_flips", {"max_buy_price": 1000, "capital": "10m"})["matching_items"] == 1
assert tools.run("get_flips", {"name_contains": "WHIP", "capital": "1b"})["matching_items"] == 1
assert tools.run("get_flips", {"sort_by": "roi", "limit": 1})["flips"][0]["name"] == "Lobster"
assert "error" in tools.run("get_flips", {"capital": "lots"})
assert len(fetches) == 1, "market should be cached between calls"
r = tools.run("get_item", {"name": "whip"})
assert r["name"] == "Abyssal whip" and r["flip"]["buy"] == 1_900_000 and r["history"]["pct_change_7d"] is not None, r
assert r["recent_market"]["direction"] in ("rising", "falling", "stable", "unknown")
assert "BUY" not in str(r) and "suggestion" not in r["recent_market"]
assert "error" in tools.run("get_item", {}) and "error" in tools.run("get_item", {"name": 5})
assert "error" in tools.run("get_item", {"name": "zzzzqqqq"})
assert "error" in tools.run("nope", {}) and "error" in tools.run("get_item", None)
assert tools.run("get_movers", {"direction": "up"})["movers"], "movers empty"
db.initialize_db()
assert isinstance(tools.run("get_news", {"limit": 3})["news"], list)
db.initialize_db()
assert "watchlist" in tools.run("get_my_tracker", {})
assert "error" in aitools.MarketTools(lambda: []).run("get_flips", {})
print("tools ok")

# ── Anthropic: thinking + text + tool call, then answer ──
F.SCRIPTS["anthropic"] = [
    [F.msg_start()] + F.a_think(0) + F.a_text(1, "Let me check. ") + F.a_tool(2, "tu_1", "get_item", {"name": "whip"}) + F.msg_end("tool_use"),
    [F.msg_start()] + F.a_text(0, "Buy it.") + F.msg_end("end_turn"),
]
chat = ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools)
text, status = [], []
res = chat.send("whip?", "sk-ant-test-key-000000", text.append, status.append, GO())
assert "".join(text) == "Let me check. Buy it.", text
assert res["tokens"] == 340, res
assert "Looking up an item…" in status
req = F.REQUESTS[-1]
second = req["body"]
headers = {k.lower(): v for k, v in req["headers"].items()}
assert headers["anthropic-beta"] == "server-side-fallback-2026-07-01", headers
assert headers["authorization"] == "Bearer sk-ant-test-key-000000"
assert second["fallbacks"] == "default" and second["output_config"] == {"effort": "medium"} and second["cache_control"] == {"type": "ephemeral"}
assert all(t["eager_input_streaming"] for t in second["tools"]) and "system" in second
print("assistant echo:", json.dumps(second["messages"][1]))
kinds = [b["type"] for b in second["messages"][1]["content"]]
assert kinds == ["thinking", "text", "tool_use"], kinds
assert second["messages"][1]["content"][0]["signature"] == "SIG123"
assert second["messages"][1]["content"][2]["input"] == {"name": "whip"}
assert all("parsed_output" not in b for b in second["messages"][1]["content"])
tr = second["messages"][2]["content"][0]
assert tr["tool_use_id"] == "tu_1" and json.loads(tr["content"])["name"] == "Abyssal whip" and tr["is_error"] is False
assert [m["role"] for m in chat.history] == ["user", "assistant", "user", "assistant"]

# A follow-up resends earlier turns byte for byte
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "ok") + F.msg_end("end_turn")]
send(chat, "and?")
after = F.REQUESTS[-1]["body"]["messages"]
assert json.dumps(after[:3]) == json.dumps(second["messages"][:3]), "history prefix changed"
assert len(after) == 5 and after[4] == {"role": "user", "content": "and?"}

# Haiku: no fallback params
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "hi") + F.msg_end("end_turn")]
send(ai.Chat(ai.ANTHROPIC, "claude-haiku-5-5", tools))
assert "fallbacks" not in F.REQUESTS[-1]["body"]

# Mid-output fallback: thinking and tool_use before the switch are dropped and not run
F.SCRIPTS["anthropic"] = [
    [F.msg_start()] + F.a_think(0) + F.a_text(1, "Part ") + F.a_tool(2, "tu_x", "get_item", {"name": "whip"}) + F.a_fallback(3) + F.a_text(4, "rest") + F.msg_end("end_turn")]
c = ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools)
st = []
c.send("q", "k" * 20, noop, st.append, GO())
assert [b["type"] for b in c.history[1]["content"]] == ["text", "text"], c.history[1]
assert "Looking up an item…" not in st

# Refusal: error, history untouched
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "") + F.msg_end("refusal")]
c = ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools)
expect_error(c, "declined")
assert c.history == []

# Cut-off tool call must not run; a cut-off reply gives a notice
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_tool(0, "tu_c", "get_item", {"name": "whip"}) + F.msg_end("max_tokens")]
expect_error(c, "cut off")
assert c.history == []
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "long") + F.msg_end("max_tokens")]
assert send(c)["notice"] == ai.NOTICE_CUT_OFF
assert len(c.history) == 2

# Tool round cap: last request forbids tools
F.SCRIPTS["anthropic"] = ([[F.msg_start()] + F.a_tool(0, f"tu_{i}", "get_news", {}) + F.msg_end("tool_use")
                           for i in range(ai.MAX_TOOL_ROUNDS)]
                          + [[F.msg_start()] + F.a_text(0, "done") + F.msg_end("end_turn")])
c = ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools)
assert send(c)["notice"] == ai.NOTICE_TOOL_LIMIT
assert F.REQUESTS[-1]["body"]["tool_choice"] == {"type": "none"} and F.REQUESTS[-2]["body"]["tool_choice"]["type"] == "auto"

# Cancel: nothing committed
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "abc") + F.msg_end("end_turn")]
c = ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools)
stop = GO()
stop.set()
send(c, cancel=stop)
assert c.history == []

# API errors
for code, msg, fragment in [(401, "invalid x-api-key", "rejected"), (404, "model: x", "recognise"),
                            (400, "Your credit balance is too low", "out of credit")]:
    F.SCRIPTS["anthropic"] = [{"status": code, "json": {"type": "error", "error": {"type": "e", "message": msg}}}]
    expect_error(ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools), fragment)
print("anthropic ok")

# ── OpenAI: reasoning + function call, then answer ──
reasoning = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "ENC"}
call = {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "get_item", "arguments": '{"name":"whip"}', "status": "completed"}
msgitem = {"id": "msg_9", "type": "message", "role": "assistant", "status": "completed",
           "content": [{"type": "output_text", "text": "Buy it.", "annotations": []}]}
done = lambda *items: {"type": "response.completed", "sequence_number": 1, "response": F.o_resp("completed", list(items))}
F.SCRIPTS["openai"] = [
    [{"type": "response.created", "sequence_number": 0, "response": F.o_resp("in_progress", [])}, done(reasoning, call)],
    [{"type": "response.output_text.delta", "sequence_number": 0, "item_id": "msg_9", "output_index": 0, "content_index": 0, "delta": "Buy it.", "logprobs": []},
     done(msgitem)],
]
chat = ai.Chat(ai.OPENAI, "gpt-6.1-sol", tools)
text, status = [], []
res = chat.send("whip?", "sk-test-000000000000000", text.append, status.append, GO())
assert "".join(text) == "Buy it." and res["tokens"] == 240, (text, res)
b = F.REQUESTS[-1]["body"]
print("openai second input:", json.dumps(b["input"])[:600])
assert b["store"] is False and b["include"] == ["reasoning.encrypted_content"] and b["reasoning"] == {"effort": "medium"} and b["stream"] is True
assert b["tools"][0]["type"] == "function" and b["tools"][0]["strict"] is False and b["instructions"].startswith("You are")
kinds = [i.get("type", i.get("role")) for i in b["input"]]
assert kinds == ["user", "reasoning", "function_call", "function_call_output"], kinds
assert b["input"][1]["encrypted_content"] == "ENC" and "parsed_arguments" not in b["input"][2]
assert json.loads(b["input"][3]["output"])["name"] == "Abyssal whip" and b["input"][3]["call_id"] == "call_1"
assert chat.history[-1]["type"] == "message"

# Bad JSON args, failed response, stream ended early, incomplete, quota
F.SCRIPTS["openai"] = [[done(dict(call, arguments='{"name":'))], [done(msgitem)]]
send(ai.Chat(ai.OPENAI, "gpt-6.1-sol", tools))
assert "error" in json.loads(F.REQUESTS[-1]["body"]["input"][-1]["output"])
fail = F.o_resp("failed", [])
fail["error"] = {"code": "server_error", "message": "boom"}
F.SCRIPTS["openai"] = [[{"type": "response.failed", "sequence_number": 1, "response": fail}]]
c = ai.Chat(ai.OPENAI, "gpt-6.1-sol", tools)
expect_error(c, "boom")
F.SCRIPTS["openai"] = [[{"type": "response.created", "sequence_number": 0, "response": F.o_resp("in_progress", [])}]]
expect_error(c, "ended before")
assert c.history == []
F.SCRIPTS["openai"] = [[{"type": "response.incomplete", "sequence_number": 1, "response": F.o_resp("incomplete", [msgitem])}]]
assert send(c)["notice"] == ai.NOTICE_CUT_OFF
F.SCRIPTS["openai"] = [{"status": 429, "json": {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}}}] * 3  # the SDK retries 429s
expect_error(ai.Chat(ai.OPENAI, "gpt-6.1-sol", tools), "out of credit")
print("openai ok")

# ── keys ──
assert ai.verify_key(ai.ANTHROPIC, "good-key-000000000000") is None and ai.verify_key(ai.OPENAI, "good-key-000000000000") is None
for p in ai.PROVIDERS:
    try:
        ai.verify_key(p, "bad-key-000000000000")
        raise AssertionError("bad key accepted")
    except ai.AIError as e:
        assert "rejected" in str(e)
assert ai.looks_like_key("sk-ant-api03-abcdefghij") and not ai.looks_like_key("short")
assert not ai.looks_like_key("sk-ant abcdefghijklmnopqrstuv") and not ai.looks_like_key("sk-ant-é" * 5)
print("verify ok")

# Credential store round trip under a throwaway provider name
tp = "UnitTest-delete-me"
assert ai.get_key(tp) is None
assert ai.save_key(tp, "sk-test-roundtrip-000000") is True
assert ai.get_key(tp) == "sk-test-roundtrip-000000"
ai.delete_key(tp)
assert ai.get_key(tp) is None
ai.delete_key(tp)    # deleting twice is harmless
print("keyring ok")
print("ALL PASSED")
