import argparse
import os
from uuid import uuid4

from membrain.client import MemoryClient, default_read_fence, prepared_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a host turn without calling an LLM.")
    parser.add_argument("--message", required=True)
    parser.add_argument("--reply", help="A reply to print and record as actually delivered.")
    args = parser.parse_args()
    branch = {"conversation_id": "example-chat", "branch_id": "main"}
    turn_id = str(uuid4())
    with MemoryClient(
        os.environ.get("MEMORY_BASE_URL", "http://127.0.0.1:8787"),
        os.environ["MEMORY_API_KEY"], os.environ["MEMORY_BRAIN_ID"],
    ) as memory:
        observed = memory.observe(
            args.message, source_event_key=f"{turn_id}:user",
            interpretation_hint="real_world_report", speech_act_hint="direct_statement", **branch,
        )
        receipt = observed["result"]
        print("Observation receipt:", receipt)
        prepared = memory.prepare(
            turn_id, latest_input_event_ids=receipt["accepted_event_ids"] + receipt["duplicate_event_ids"],
            read_fence=default_read_fence(receipt["durable_through_sequence"]), **branch,
        )
        print("Context for the host to inject before inference:", prepared_context(prepared))
        if args.reply is not None:
            print(args.reply, flush=True)
            delivered = memory.observe(
                args.reply, source_event_key=f"{turn_id}:assistant", role="assistant",
                delivery_state="delivered", **branch,
            )["result"]
            working = prepared["result"]["working_state"]
            committed = memory.commit(
                turn_id, expected_working_revision=working["revision"],
                delivered_event_ids=delivered["accepted_event_ids"] + delivered["duplicate_event_ids"],
                proposed_items=working["items"], proposed_open_loops=working["open_loops"], **branch,
            )["result"]
            print("Checkpoint:", memory.checkpoint(
                expected_working_revision=committed["working_state"]["revision"],
                through_sequence=delivered["durable_through_sequence"], reason="shutdown", **branch,
            ))


if __name__ == "__main__":
    main()
