"""LongMemEval (S) end-to-end answer accuracy for memBrain (roadmap A6).

LongMemEval asks 500 questions, each about its own history of about 50 user-assistant chat sessions (about
115,000 tokens): single-session user and assistant facts, preferences, multi-session aggregation, knowledge
updates, temporal reasoning, and 30 unanswerable questions.

Arms, answered by the same host model and judged with LongMemEval's own per-type judge prompts
(``src/evaluation/evaluate_qa.py``, MIT licence), used verbatim:

- ``N``: no history.
- ``F``: the full history in the prompt, formatted as in LongMemEval's reader.
- ``M``: memBrain through the plugin path. One installation holds every question's history in its own user memory
  (``host`` multi-user brains), one conversation per session, each message prefixed with its session date and
  stored with its real role. The question then starts a new session: session context plus recall for the
  question, with up to two ``memory_recall`` calls. Recording is paused for the question.

The answer prompt is LongMemEval's non-step-by-step reader prompt. Usage::

    python evaluations/longmemeval/run_longmemeval.py --data longmemeval_s_cleaned.json --out evaluations/longmemeval/run-1
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE.parent / "locomo"))
from run_locomo import Model, install_model, log, start  # noqa: E402

READER = ("I will give you several history chats between you and a user. Please answer the question based on the "
          "relevant chat history.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer:")
MEMORY_GUIDE = ("The history chats above come from memBrain, the long-term memory of your conversations with this "
                "user. If they do not answer the question, call memory_recall with a few concrete keywords; one or "
                "two searches are usually enough.")
RECALL_TOOL = [{"type": "function", "function": {
    "name": "memory_recall", "description": "Search the long-term memory of your conversations with this user.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]


def judge_prompt(task: str, question: str, answer: str, response: str, abstention: bool) -> str:
    """LongMemEval ``get_anscheck_prompt``, verbatim."""
    if not abstention:
        if task in ['single-session-user', 'single-session-assistant', 'multi-session']:
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."  # noqa: E501
        elif task == 'temporal-reasoning':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."  # noqa: E501
        elif task == 'knowledge-update':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."  # noqa: E501
        elif task == 'single-session-preference':
            template = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."  # noqa: E501
        else:
            raise NotImplementedError(task)
    else:
        template = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."  # noqa: E501
    return template.format(question, answer, response)


def history_text(entry: dict) -> str:
    """The full history in LongMemEval's reader format (sessions with dates, turns as JSON)."""
    parts = []
    for index, (date, session) in enumerate(zip(entry["haystack_dates"], entry["haystack_sessions"]), 1):
        turns = [{"role": turn["role"], "content": turn["content"]} for turn in session]
        parts.append(f"### Session {index}:\nSession Date: {date}\nSession Content:\n{json.dumps(turns)}")
    return "\n\n".join(parts)


def user_of(entry: dict) -> str:
    return "lme:" + entry["question_id"]


def ingest(entry: dict, directory: Path) -> int:
    """Store the question's history in its own user memory, as hooks would; idempotent."""
    from membrain import host

    user = user_of(entry)
    marker = directory / "ingested" / f"{entry['question_id']}.json"
    if marker.exists():
        return json.loads(marker.read_text())["messages"]
    stored = 0
    with host.ready_client(directory, user) as client:
        for index, (date, session) in enumerate(zip(entry["haystack_dates"], entry["haystack_sessions"])):
            conversation = f"{entry['question_id']}-s{index}"
            for number, turn in enumerate(session):
                if not turn["content"].strip():
                    continue
                record = host.message_record(conversation, turn["role"], f"({date}) {turn['content']}",
                                             turn_id=str(number), producer="longmemeval-ingest", user_id=user)
                stored += host.deliver(directory, client, record)
    host.pause(directory, user_id=user)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"messages": stored}))
    return stored


def wait_for_embeddings(directory: Path, user: str, timeout: float = 3600) -> None:
    from membrain import host

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = host.ready_client(directory, user)
        if client is not None:  # None while the service restarts; keep waiting.
            with client:
                status = client.dashboard()["status"]
            if status.get("pending_jobs") == 0 and status.get("index_lag", 0) == 0:
                return
        time.sleep(3)
    raise RuntimeError("embeddings did not finish")


def answer_with_membrain(model: Model, entry: dict, directory: Path) -> dict:
    from membrain import host

    user, session = user_of(entry), "question-" + entry["question_id"]
    context = host.session_context(directory, session, user_id=user)
    recalled = host.prompt_context(directory, session, entry["question"], user_id=user) or ""
    history = "\n\n".join(part for part in (context, recalled) if part)
    messages = [{"role": "system", "content": MEMORY_GUIDE},
                {"role": "user", "content": READER.format(history, entry["question_date"], entry["question"])}]
    searches = []
    for _ in range(3):
        message = model.chat(messages, tools=RECALL_TOOL if len(searches) < 2 else None, max_tokens=500)
        calls = message.get("tool_calls") or []
        if not calls:
            return {"answer": (message.get("content") or "").strip(), "searches": searches,
                    "context_characters": len(history)}
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            try:
                query = json.loads(call["function"]["arguments"]).get("query", "")
            except (ValueError, KeyError, TypeError):
                query = ""
            with host.ready_client(directory, user) as client:
                found = host.recall_context(client, host.scrub(str(query)), current_conversation=session)
            searches.append(query)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": found or "No matching records."})
    return {"answer": "", "searches": searches, "context_characters": len(history)}


def answer(model: Model, arm: str, entry: dict, directory: Path) -> dict:
    if arm == "M":
        return answer_with_membrain(model, entry, directory)
    history = history_text(entry) if arm == "F" else "(none)"
    prompt = READER.format(history, entry["question_date"], entry["question"])
    return {"answer": (model.chat([{"role": "user", "content": prompt}], max_tokens=500).get("content") or "").strip()}


def judge(model: Model, entry: dict, response: str) -> dict:
    abstention = entry["question_id"].endswith("_abs")
    message = model.chat([{"role": "user", "content": judge_prompt(
        entry["question_type"], entry["question"], entry["answer"], response, abstention)}], max_tokens=10)
    label = (message.get("content") or "").strip().lower()
    return {"label": label, "correct": "yes" in label}


def select(data: list, sample: int, seed: int) -> list:
    """All questions, or a sample stratified by question type (abstention questions kept in proportion)."""
    if not sample or sample >= len(data):
        return data
    groups = defaultdict(list)
    for entry in data:
        groups[(entry["question_type"], entry["question_id"].endswith("_abs"))].append(entry)
    rng, chosen = random.Random(seed), []
    for key in sorted(groups):
        items = groups[key]
        chosen += rng.sample(items, max(1, round(sample * len(items) / len(data))))
    return sorted(chosen, key=lambda entry: entry["question_id"])


def run(args) -> None:
    data = select(json.loads(Path(args.data).read_text()), args.sample, args.seed)
    if args.shard:  # Run part of the sample, e.g. "2/3", in its own output directory and installation.
        index, count = map(int, args.shard.split("/"))
        data = data[index - 1::count]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    arms = args.arms.split(",")
    usage_path = out / f"usage-{'-'.join(arms)}{'-' + args.label if args.label else ''}.json"
    earlier = json.loads(usage_path.read_text()) if usage_path.exists() else {}
    model = Model(args.base_url, args.model, cap=len(data) * len(arms) * 5 + 20, thinking=args.thinking)

    def save_usage():
        totals = dict(earlier)
        for key, value in {**model.usage, "calls": model.calls}.items():
            totals[key] = earlier.get(key, 0) + value
        usage_path.write_text(json.dumps(totals, indent=2))

    directory = out / "brain"
    for arm in arms:
        name = f"{arm}-{args.label}" if args.label else arm
        path = out / f"answers-{name}.jsonl"
        done = {json.loads(line)["id"] for line in path.read_text().splitlines()} if path.exists() else set()
        if arm == "M":
            from membrain import host

            if args.ports and not (directory / "profile.json").exists():
                # Fixed ports keep this run clear of other installations started on the same machine.
                from membrain.embedded_database import default_engine
                from membrain.local_profile import ensure_profile

                api, database = map(int, args.ports.split(","))
                ensure_profile(directory, api_port=api, database_port=database, database_engine=default_engine())
            host.ensure_installation(directory)
            if not args.no_semantic:
                install_model(directory, out / "model-cache")
            start(directory)
        for entry in data:
            if entry["question_id"] in done:
                continue
            started = time.monotonic()
            if arm == "M":
                stored = ingest(entry, directory)
                wait_for_embeddings(directory, user_of(entry))
                log("ingested", entry["question_id"], stored, f"{time.monotonic() - started:.0f}s")
            result = answer(model, arm, entry, directory)
            verdict = judge(model, entry, result["answer"])
            row = {"id": entry["question_id"], "arm": name, "type": entry["question_type"],
                   "abstention": entry["question_id"].endswith("_abs"), "question": entry["question"],
                   "gold": entry["answer"], **result, **verdict, "seconds": round(time.monotonic() - started, 1)}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            save_usage()
            log(name, entry["question_id"], "correct" if verdict["correct"] else "wrong", "|", result["answer"][:60])
        if arm == "M":  # Free the service port for the next run.
            from membrain import desktop
            try:
                desktop.stop(directory)
            except desktop.DesktopError:
                pass
    save_usage()


def report(args) -> None:
    out = Path(args.out)
    types = ["single-session-user", "single-session-assistant", "single-session-preference", "multi-session",
             "knowledge-update", "temporal-reasoning"]
    lines = ["| Arm | " + " | ".join(types) + " | Abstention | Overall | Questions |", "|" + " --- |" * 10]
    summary = {}
    for path in sorted(out.glob("answers-*.jsonl")):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if not rows:
            continue
        arm, cells = rows[0]["arm"], []
        for kind in types:
            values = [row["correct"] for row in rows if row["type"] == kind and not row["abstention"]]
            cells.append(f"{100 * sum(values) / len(values):.1f}% ({len(values)})" if values else "—")
        abstention = [row["correct"] for row in rows if row["abstention"]]
        cells.append(f"{100 * sum(abstention) / len(abstention):.1f}% ({len(abstention)})" if abstention else "—")
        overall = [row["correct"] for row in rows]
        summary[arm] = {"overall": sum(overall) / len(overall), "questions": len(overall)}
        lines.append(f"| {arm} | " + " | ".join(cells) + f" | {100 * sum(overall) / len(overall):.1f}% | {len(overall)} |")
    usage = defaultdict(int)
    for path in out.glob("usage*.json"):
        for key, value in json.loads(path.read_text()).items():
            usage[key] += value
    summary["usage"] = dict(usage)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\n".join(lines))
    print("usage:", json.dumps(dict(usage)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", required=True, help="longmemeval_s_cleaned.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--arms", default="N,F,M")
    parser.add_argument("--sample", type=int, default=0, help="Stratified sample size; 0 means all 500")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--ports", default="", help="API and database ports for a new installation, e.g. 8801,55441")
    parser.add_argument("--shard", default="", help="Run only part i of n of the sample, e.g. 1/3")
    parser.add_argument("--label", default="")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--no-semantic", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    report(args) if args.report else run(args)


if __name__ == "__main__":
    main()
