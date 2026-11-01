"""Exercise an already running API and worker, including persistent history."""

import argparse
import json
import time
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8010")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    headers = {"X-Student-ID": "smoke-" + str(uuid.uuid4())}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    with httpx.Client(base_url=args.url, headers=headers, timeout=120) as client:

        def request(method, path, **kwargs):
            result = client.request(method, path, **kwargs)
            result.raise_for_status()
            return result.json()

        course = json.loads(Path("data/data-science.json").read_text())
        imported = request("POST", "/materials/import", json=course)
        dialog = request("POST", "/dialogs", json={"course_id": course["course"]["id"]})
        message = request(
            "POST",
            f"/dialogs/{dialog['id']}/messages",
            json={"text": "Как работать с таблицами Pandas?", "request_key": str(uuid.uuid4())},
        )
        deadline = time.monotonic() + 120
        while message["status"] in ("queued", "processing") and time.monotonic() < deadline:
            time.sleep(0.5)
            message = request("GET", f"/messages/{message['id']}")
        assert message["status"] == "completed", message
        assert message["sources"], message
        assert all(s["course_id"] == course["course"]["id"] for s in message["sources"])
        history = request("GET", f"/dialogs/{dialog['id']}/messages")
        assert history[0]["answer"] == message["answer"]
        print(
            json.dumps(
                {
                    "import": imported,
                    "dialog_id": dialog["id"],
                    "message_id": message["id"],
                    "status": message["status"],
                    "source_count": len(message["sources"]),
                    "model": message["model"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
