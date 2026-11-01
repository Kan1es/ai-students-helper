"""Reproducible retrieval evaluation on the supplied course; no LLM calls."""

import argparse
import json
import tempfile
from pathlib import Path

from app.db import database, initialize
from app.materials import import_course
from app.schemas import CourseImport
from app.search import retrieve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", type=Path, default=Path("data/data-science.json"))
    parser.add_argument("--questions", type=Path, default=Path("data/evaluation.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/retrieval-results.json"))
    args = parser.parse_args()
    data = CourseImport.model_validate_json(args.course.read_text())
    results = []
    with tempfile.TemporaryDirectory() as temp:
        engine, sessions = database(f"sqlite:///{temp}/eval.db")
        initialize(engine)
        with sessions.begin() as session:
            imported = import_course(session, data)
        with sessions() as session:
            for case in json.loads(args.questions.read_text()):
                chunks = retrieve(session, data.course.id, data.materials_version, case["question"], None)
                found = list(dict.fromkeys(c.lesson_id for c in chunks))
                passed = (
                    not found
                    if case.get("expect_empty")
                    else bool(set(found) & set(case["expected_lesson_ids"]))
                )
                results.append({**case, "retrieved_lesson_ids": found, "passed": passed})
        engine.dispose()
    report = {
        "mode": "retrieval_only",
        "semantic_answers_reviewed": False,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "import": imported,
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Retrieval checks: {report['passed']}/{report['total']}; {args.output}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
