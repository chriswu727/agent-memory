import argparse
import json
import os

from agent_memory.client import MemoryClient
from agent_memory.runtime import BrainRuntime, DeliveryReceipt


def main():
    parser = argparse.ArgumentParser(description="Exercise automatic memory without calling a model.")
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--conversation", default="autonomous-example")
    parser.add_argument("--message", required=True)
    parser.add_argument("--reply", required=True)
    args = parser.parse_args()

    def generate(request):
        print(json.dumps({"turn_id": request.turn_id, "input_budget_used": request.input_tokens_estimate}))
        return args.reply

    def deliver(request):
        print(request.text, flush=True)
        return DeliveryReceipt("delivered")

    with MemoryClient(os.environ.get("MEMORY_BASE_URL", "http://127.0.0.1:8787"),
                      os.environ["MEMORY_API_KEY"], os.environ["MEMORY_BRAIN_ID"]) as memory:
        runtime = BrainRuntime(memory, generate, deliver=deliver)
        result = runtime.respond(args.message, message_id=args.message_id, conversation_id=args.conversation)
        print(json.dumps({"turn_id": result.turn_id, "stage": result.stage, "replayed": result.replayed}))


if __name__ == "__main__":
    main()
