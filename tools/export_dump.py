"""Read PostgreSQL COPY text without executing SQL or restoring unrelated data."""

import argparse
import hashlib
import json
import re
from pathlib import Path

TABLES = {"room_room", "filemanager_lessonblocks", "filemanager_file"}


def unescape(value):
    if value == r"\N":
        return None
    replacements = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "\\": "\\"}
    return re.sub(
        r"\\([0-7]{1,3}|x[0-9a-fA-F]{1,2}|.)",
        lambda m: (
            chr(int(m[1], 16))
            if m[1].startswith("x")
            else chr(int(m[1], 8))
            if m[1][0].isdigit()
            else replacements.get(m[1], m[1])
        ),
        value,
    )


def read_tables(path: Path):
    rows = {t: [] for t in TABLES}
    table, columns = None, []
    with path.open() as stream:
        for line in stream:
            if line.startswith("COPY public."):
                match = re.match(r"COPY public\.(\w+) \((.*?)\) FROM stdin;", line)
                table = match[1] if match and match[1] in TABLES else None
                columns = [c.strip(' "') for c in match[2].split(",")] if table else []
            elif line.rstrip("\n") == r"\.":
                table = None
            elif table:
                values = line.rstrip("\n").split("\t")
                if len(values) != len(columns):
                    raise ValueError(f"Invalid COPY row in {table}")
                rows[table].append(dict(zip(columns, map(unescape, values))))
    return rows


def export(path: Path, slug: str, lesson_url_template: str | None = None):
    tables = read_tables(path)
    matches = [r for r in tables["room_room"] if r["slug"] == slug and r["delobj"] != "t"]
    if len(matches) != 1:
        raise ValueError(f"Expected one active course for slug={slug}, found {len(matches)}")
    course = matches[0]
    blocks = [
        {"id": int(b["id"]), "title": b["name"], "position": int(b["sort"])}
        for b in tables["filemanager_lessonblocks"]
        if b["room_id"] == course["uuid"]
    ]
    lessons = []
    for row in tables["filemanager_file"]:
        if row["room_id"] != course["uuid"] or row["delobj"] == "t":
            continue
        text = row["text"] or ""
        url = (
            lesson_url_template.format(course_id=course["uuid"], lesson_id=row["uuid"], slug=slug)
            if lesson_url_template
            else None
        )
        lessons.append(
            {
                "id": row["uuid"],
                "block_id": int(row["lesson_block_id"]),
                "position": int(row["sort"]),
                "title": row["name"],
                "type": row["type_lesson"],
                "text": text,
                "summary": row["compressed_text"],
                "source_url": url,
                "source_file": row["file"],
                "video_duration_ms": int(row["duration_ms"]) if row["duration_ms"] else None,
                "updated_at": row["updated"],
                "text_sha256": hashlib.sha256(text.encode()).hexdigest() if text else None,
                "content_kind": "lesson_text" if text else None,
            }
        )
    result = {
        "schema_version": 1,
        "course": {
            "id": course["uuid"],
            "slug": slug,
            "title": course["name"],
            "description": course["description"] or "",
        },
        "blocks": sorted(blocks, key=lambda b: (b["position"], b["id"])),
        "lessons": sorted(lessons, key=lambda lesson: (lesson["block_id"], lesson["position"], lesson["id"])),
    }
    result["materials_version"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    from app.schemas import CourseImport

    return CourseImport.model_validate(result).model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("--course", required=True, help="Course slug")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lesson-url-template", help="Verified route with {course_id}, {lesson_id}, {slug}")
    args = parser.parse_args()
    result = export(args.dump, args.course, args.lesson_url_template)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Exported {len(result['lessons'])} lessons to {args.output}")


if __name__ == "__main__":
    main()
