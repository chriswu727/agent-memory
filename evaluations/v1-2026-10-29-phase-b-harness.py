"""Phase B (B7) held-out run: plugin now (P), plugin before Phase B (O), full history (F), no memory (N).

P and O run the plugin's own hook functions and MCP tools in process against their own embedded installation, the
way Claude Code calls them: session_context at session start, prompt_context before each message, finish_turn after
the reply, and the memory_recall, memory_note, memory_pause and memory_resume tools with the version's own schemas
and descriptions. The version's skill text is in the system prompt. O imports the older source tree given by
MEMBRAIN_SRC (a checkout of the commit before Phase B). Role-play sessions (ids starting "roleplay-") use the
role-play memory in P; O has none. The host is told the scenario's date, as Claude Code tells the model today's date,
and P's coming-up block uses the same date.

Usage: MEMBRAIN_SRC=... MEMBRAIN_MODEL_CACHE=... MEMBRAIN_PORTS=api,db python v1-2026-10-29-phase-b-harness.py ARM SCENARIO_FILE OUT_DIR
Resumable per arm and turn; hard call cap.
"""
import asyncio
import datetime
import importlib.util
import json
import os
import re
import sys
import time
import types
from pathlib import Path

import httpx

SOURCE = Path(os.environ.get("MEMBRAIN_SRC", Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(SOURCE))

BASE_URL, MODEL = "https://api.deepseek.com", os.environ.get("HOST_MODEL", "deepseek-v4-pro")
YEAR = 2026
TOOLS = ("memory_recall", "memory_note", "memory_pause", "memory_resume")
MONTHS = {name: number for number, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], 1)}
MESES = {name: number for number, name in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
     "diciembre"], 1)}


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def session_date(text: str, previous: datetime.date | None) -> datetime.date | None:
    """The scenario date stated in a session's first message ("Today is Tuesday, March 3", Spanish or Chinese)."""
    if match := re.search(r"Today is \w+, (\w+) (\d+)", text):
        return datetime.date(YEAR, MONTHS[match[1].lower()], int(match[2]))
    if match := re.search(r"Hoy es \w+, (\d+) de (\w+)", text):
        return datetime.date(YEAR, MESES[match[2].lower()], int(match[1]))
    if match := re.search(r"今天是(\d+)月(\d+)日", text):
        return datetime.date(YEAR, int(match[1]), int(match[2]))
    return previous


def simulate_today(today: datetime.date) -> None:
    """Make the notes module (coming-up block) see the scenario date."""
    try:
        from membrain import notes
    except ImportError:
        return
    if not hasattr(notes, "_datetime"):
        return

    class ScenarioDate(datetime.date):
        @classmethod
        def today(cls):
            return today

    shim = types.ModuleType("datetime")
    shim.__dict__.update(datetime.__dict__)
    shim.date = ScenarioDate
    notes._datetime = shim


class Host:
    def __init__(self, cap):
        self.calls, self.cap = 0, cap
        self.client = httpx.Client(timeout=600)

    def call(self, messages, tools=None):
        if self.calls >= self.cap:
            raise RuntimeError("host call cap reached")
        body = {"model": MODEL, "messages": messages, "max_tokens": 16384, "stream": False,
                "thinking": {"type": "enabled"}}
        if tools:
            body["tools"] = tools
        for attempt in range(4):
            self.calls += 1
            try:
                response = self.client.post(BASE_URL + "/chat/completions", json=body,
                                            headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]})
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"], data.get("usage", {})
            except (httpx.HTTPError, KeyError, ValueError) as error:
                log("host error", type(error).__name__, str(error)[:300])
                if attempt == 3 or self.calls >= self.cap:
                    raise
                time.sleep(10 * (attempt + 1))


def add_usage(total, usage):
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key, 0) or 0)


class Memory:
    """The plugin's hooks and MCP tools for one installation, from whichever source tree is on the path."""

    def __init__(self, directory: Path, model_cache: Path | None, ports: tuple[int, int] | None = None):
        import shutil

        from membrain import host

        self.host, self.directory = host, directory
        if ports and not (directory / "profile.json").exists():
            # Fixed ports keep this run clear of other installations started on the same machine.
            from membrain.embedded_database import default_engine
            from membrain.local_profile import ensure_profile

            ensure_profile(directory, api_port=ports[0], database_port=ports[1], database_engine=default_engine())
        host.ensure_installation(directory)
        if model_cache is not None:  # The local embedding model, downloaded once, as the plugin would fetch it.
            from membrain.local_embeddings import download, installed, model_directory

            if not installed(directory):
                if not installed(model_cache):
                    download(model_cache)
                shutil.copytree(model_directory(model_cache), model_directory(directory), dirs_exist_ok=True)
        from membrain import desktop

        try:
            host.start(directory)
        except desktop.DesktopError:  # A slow start on a loaded machine is waited for, not fatal.
            deadline = time.monotonic() + 600
            while not desktop.status(directory).get("ready"):
                if time.monotonic() > deadline:
                    raise
                time.sleep(5)
        skill = SOURCE.parent / "plugins" / "membrain" / "skills" / "membrain" / "SKILL.md"
        self.skill = skill.read_text().split("---", 2)[2].strip()
        self.roleplay_supported = hasattr(host, "roleplay_user")

    def options(self, roleplay):
        return {"roleplay": roleplay} if roleplay and self.roleplay_supported else {}

    def tools(self, roleplay):
        from membrain.mcp_server import create_server

        user = self.host.roleplay_user(None, roleplay) if roleplay and self.roleplay_supported else None
        client = self.host.ready_client(self.directory, user)
        if client is None:
            raise RuntimeError("memory service is not ready")
        server = create_server(client, directory=self.directory)
        listed = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        schemas = [{"type": "function", "function": {
            "name": name, "description": listed[name].description or "", "parameters": listed[name].inputSchema}}
            for name in TOOLS if name in listed]
        return client, server, schemas

    def call(self, server, name, arguments) -> str:
        try:
            result = asyncio.run(server.call_tool(name, arguments))
        except Exception as error:  # The host sees tool errors, as it would through MCP.
            return json.dumps({"error": str(error)[:500]})
        if isinstance(result, tuple):
            result = result[1] if result[1] is not None else result[0]
        if isinstance(result, list):
            return "\n".join(getattr(part, "text", str(part)) for part in result)
        return json.dumps(result, ensure_ascii=False)

    def wait_until_indexed(self, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            client = self.host.ready_client(self.directory)
            if client is not None:
                with client:
                    status = client.dashboard()["status"]
                if status.get("pending_jobs") == 0 and status.get("index_lag", 0) == 0:
                    return
            time.sleep(2)


def load(path: str):
    spec = importlib.util.spec_from_file_location("scenario", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(arm: str, scenario_path: str, out: Path) -> None:
    module = load(scenario_path)
    sessions = module.SESSIONS_LONG
    scenario = Path(scenario_path).stem
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"results_{scenario}_{arm}.jsonl"
    done = {(row["session"], row["turn"]): row for row in map(json.loads, path.read_text().splitlines())} \
        if path.exists() else {}
    host = Host(cap=sum(len(turns) for _, turns in sessions) * 5)
    memory = None
    if arm in ("P", "O"):
        cache = os.environ.get("MEMBRAIN_MODEL_CACHE")
        ports = tuple(map(int, os.environ["MEMBRAIN_PORTS"].split(","))) if os.environ.get("MEMBRAIN_PORTS") else None
        memory = Memory(out / f"brain-{scenario}-{arm}", Path(cache) if cache else None, ports)
    history, today = [], None
    for session, turns in sessions:
        today = session_date(turns[0], today)
        simulate_today(today)
        roleplay = session.split("-")[2] if session.startswith("roleplay-") else None
        date_line = f"Today's date: {today.isoformat()} ({today.strftime('%A')})."
        session_context = None
        if memory is not None and not all((session, index) in done for index in range(len(turns))):
            memory.wait_until_indexed()
            session_context = memory.host.session_context(memory.directory, session, **memory.options(roleplay))
        current = []
        for index, text in enumerate(turns):
            key = (session, index)
            if key in done:
                row = done[key]
                current += row["transcript"]
                continue
            system, prompt_context, schemas, client, server = module.HOST + "\n\n" + date_line, None, None, None, None
            if memory is not None:
                prompt_context = memory.host.prompt_context(memory.directory, session, text, turn_id=f"t{index}",
                                                            **memory.options(roleplay))
                client, server, schemas = memory.tools(roleplay)
                system += "\n\n" + memory.skill + ("\n\n" + session_context if session_context else "")
            user_content = (prompt_context + "\n\n" if prompt_context else "") + text
            prior = history if arm == "F" else []
            messages = [{"role": "system", "content": system}, *prior, *current, {"role": "user", "content": user_content}]
            usage, started, tool_calls = {}, time.monotonic(), []
            try:
                for _ in range(4):  # At most three tool rounds, then a final answer.
                    message, call_usage = host.call(messages, schemas if schemas and len(tool_calls) < 6 else None)
                    add_usage(usage, call_usage)
                    calls = message.get("tool_calls") or []
                    if not calls:
                        break
                    messages.append({k: v for k, v in message.items()
                                     if k in ("role", "content", "tool_calls", "reasoning_content")})
                    for call in calls:
                        try:
                            arguments = json.loads(call["function"]["arguments"] or "{}")
                        except ValueError:
                            arguments = {}
                        result = memory.call(server, call["function"]["name"], arguments)
                        tool_calls.append({"name": call["function"]["name"], "arguments": arguments,
                                           "result": result[:2000]})
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            finally:
                if client is not None:
                    client.close()
            reply = message.get("content") or ""
            if memory is not None:
                memory.host.finish_turn(memory.directory, session, reply, turn_id=f"t{index}",
                                        **memory.options(roleplay))
            # The turn as the host's transcript keeps it: tool calls and results stay visible in later turns.
            transcript = [{k: v for k, v in item.items() if k != "reasoning_content"}
                          for item in messages[1 + len(prior) + len(current):]]
            transcript.append({"role": "assistant", "content": reply})
            row = {"arm": arm, "scenario": scenario, "session": session, "turn": index, "date": today.isoformat(),
                   "input": text, "reply": reply, "usage": usage, "seconds": round(time.monotonic() - started, 1),
                   "host_user_content": user_content, "session_context": session_context or "",
                   "prompt_context_chars": len(prompt_context or ""), "tool_calls": tool_calls,
                   "transcript": transcript}
            with path.open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            log(arm, scenario, session, index, usage.get("total_tokens"),
                "tools:", [(call["name"], str(call["arguments"])[:80]) for call in tool_calls])
            current += transcript
        history += current
    if memory is not None:
        from membrain import desktop

        desktop.stop(memory.directory)
    log(arm, scenario, "finished")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
