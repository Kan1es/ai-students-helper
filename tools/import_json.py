import argparse
import json
from pathlib import Path

import httpx

from app.schemas import CourseImport


def main():
    parser = argparse.ArgumentParser(description="Validate and upload a course JSON")
    parser.add_argument("file", type=Path)
    parser.add_argument("--url", default="http://localhost:8010")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    data = CourseImport.model_validate_json(args.file.read_text())
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    response = httpx.post(
        args.url.rstrip("/") + "/materials/import",
        json=data.model_dump(mode="json"),
        headers=headers,
        timeout=120,
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
