"""A platform-style agent (Gemini Flash, FREE tier key from ~/revenue-agent/.env) driving the Ribhiya MCP server over
HTTP with the Wesam package as its instructions. Measures real token usage reported by the provider.

usage: uv run --project ~/revenue-agent python -I wesam_agent_test.py <mcp_url> <bearer> <raw_data_dir> <out.json>
The owner's side is simulated: when the agent gives an upload link, the files are POSTed to it (as a browser would).
"""
import asyncio, glob, json, os, re, sys, time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL, TOKEN, DATA, OUT = sys.argv[1:5]
ENV = dict(l.split("=", 1) for l in Path.home().joinpath("revenue-agent/.env").read_text().splitlines()
           if l.startswith("GEMINI_API_KEY="))
KEY = ENV["GEMINI_API_KEY"].strip().split(" #")[0]
BASE = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODEL = os.environ.get("TEST_MODEL", "gemini-3.5-flash")
SYSTEM = (Path.home() / "revenue-agent/wesam/INSTRUCTIONS.md").read_text() + "\n\n" + \
         (Path.home() / "revenue-agent/wesam/ribhiya-profit.skill").read_text()


def chat(messages, tools):
    for attempt in range(4):
        r = httpx.post(BASE, headers={"Authorization": f"Bearer {KEY}"}, timeout=120,
                       json={"model": MODEL, "messages": messages, "tools": tools, "temperature": 0.2,
                             "reasoning_effort": "low"})
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate limited")


async def main():
    log = {"model": MODEL, "turns": [], "usage": {"prompt": 0, "completion": 0, "total": 0, "calls": 0}}
    async with streamablehttp_client(MCP_URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                      "parameters": t.inputSchema}} for t in (await s.list_tools()).tools]
            messages = [{"role": "system", "content": SYSTEM}]
            owner_turns = ["عندي محل هدايا أونلاين اسمه Gift Corner، الفلوس بالجنيه الاسترليني GBP. بقالي كام شهر ببيع "
                           "كويس بس حاسس إن الربح قلّ. ساعدني أعرف ليه.",
                           "رفعت الملفات. قولي إيه اللي حصل؟",
                           "طب فيه طريقة أزوّد بيها مبيعاتي؟"]
            for owner in owner_turns:
                messages.append({"role": "user", "content": owner})
                log["turns"].append({"owner": owner, "tool_calls": []})
                for _ in range(8):
                    t0 = time.perf_counter()
                    resp = chat(messages, tools)
                    u = resp.get("usage", {})
                    log["usage"]["prompt"] += u.get("prompt_tokens", 0)
                    log["usage"]["completion"] += u.get("completion_tokens", 0)
                    log["usage"]["total"] += u.get("total_tokens", 0)
                    log["usage"]["calls"] += 1
                    msg = resp["choices"][0]["message"]
                    messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
                    if not msg.get("tool_calls"):
                        log["turns"][-1]["agent"] = msg.get("content")
                        break
                    for tc in msg["tool_calls"]:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                        res = await s.call_tool(tc["function"]["name"], args)
                        text = res.content[0].text
                        log["turns"][-1]["tool_calls"].append({"tool": tc["function"]["name"], "args": args,
                                                               "result_chars": len(text)})
                        link = re.search(r'"url":\s*"([^"]+/u/[^"]+)"', text)
                        if link:  # the owner opens the link and uploads (simulated browser upload)
                            files = [("files", (Path(f).name, open(f, "rb").read(), "text/csv"))
                                     for f in [f"{DATA}/product_costs.csv"] + sorted(glob.glob(f"{DATA}/2011-*/*.csv"))]
                            up = httpx.post(link.group(1) + "/files", files=files, timeout=120)
                            log["turns"][-1]["owner_uploaded"] = up.status_code
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": text})
                    time.sleep(8)  # stay inside the free-tier rate limit
    Path(OUT).write_text(json.dumps(log, ensure_ascii=False, indent=1))
    for t in log["turns"]:
        print("OWNER:", t["owner"]); print("  tools:", [c["tool"] for c in t["tool_calls"]])
        print("AGENT:", (t.get("agent") or "")[:700], "\n")
    print("USAGE:", log["usage"])

asyncio.run(main())
