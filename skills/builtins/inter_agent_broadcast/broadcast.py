"""
NWO Skill: Inter-Agent Message Broadcast
Runtime: Python
Entry point: broadcast.py

Posts structured messages to the NWO Agent Graph as typed nodes.
Other agents can subscribe to graph updates to receive messages.
"""

import json
import os
import time
import uuid


def load_inputs() -> dict:
    return json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))


def write_outputs(outputs: dict) -> None:
    out_file = os.environ.get("NWO_SKILL_OUTPUT_FILE")
    if out_file:
        with open(out_file, "w") as f:
            json.dump(outputs, f, indent=2)
    else:
        print(json.dumps(outputs))


def post_graph_node(
    api_url: str,
    sender_id: str,
    target_id: str | None,
    message_type: str,
    payload: dict,
    ttl_seconds: int,
) -> str | None:
    """Post a message node to the NWO Agent Graph. Returns node ID or None on failure."""
    try:
        import httpx

        node_content = {
            "type": f"message.{message_type}",
            "sender_agent_id": sender_id,
            "target_agent_id": target_id,
            "payload": payload,
            "timestamp": time.time(),
            "ttl_seconds": ttl_seconds,
            "message_id": str(uuid.uuid4()),
        }

        r = httpx.post(
            f"{api_url}/nodes",
            json={
                "label": f"MSG:{message_type.upper()}",
                "content": json.dumps(node_content),
                "owner_agent_id": sender_id,
                "node_type": "communication",
            },
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json().get("id")
    except Exception:
        return None


def resolve_recipients(api_url: str, target_agents: list) -> list[str]:
    """Resolve recipient list — if empty, fetch all active agents from graph."""
    if target_agents:
        return list(target_agents)
    try:
        import httpx
        r = httpx.get(f"{api_url}/agents", timeout=10.0)
        r.raise_for_status()
        agents = r.json()
        return [a["id"] for a in agents if isinstance(a, dict) and "id" in a]
    except Exception:
        return []


def main():
    inputs = load_inputs()

    message = inputs.get("message", {})
    target_agents = list(inputs.get("target_agents", []))
    message_type = str(inputs.get("message_type", "status_update"))
    api_url = str(inputs.get("graph_api_url", "http://localhost:8000")).rstrip("/")
    sender_id = str(inputs.get("sender_agent_id", "unknown"))
    ttl_seconds = int(inputs.get("ttl_seconds", 3600))

    recipients = resolve_recipients(api_url, target_agents)
    broadcast = len(recipients) == 0  # True = broadcast to all

    node_ids = []
    delivered = 0
    failed = 0

    if broadcast or not recipients:
        # Single broadcast node (no specific target)
        node_id = post_graph_node(api_url, sender_id, None, message_type, message, ttl_seconds)
        if node_id:
            node_ids.append(node_id)
            delivered += 1
        else:
            failed += 1
        recipients = ["*broadcast*"]
    else:
        for target_id in recipients:
            node_id = post_graph_node(api_url, sender_id, target_id, message_type, message, ttl_seconds)
            if node_id:
                node_ids.append(node_id)
                delivered += 1
            else:
                failed += 1

    write_outputs({
        "node_ids": node_ids,
        "recipients": recipients,
        "delivered_count": delivered,
        "failed_count": failed,
    })


if __name__ == "__main__":
    main()
