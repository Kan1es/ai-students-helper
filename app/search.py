import math
import re
from collections import Counter

import snowballstemmer
from sqlalchemy import select

from app.models import Chunk

STEMMER = snowballstemmer.stemmer("russian")
STOP = set(
    "и в на по с со для из а но как что это мне я ты мы вы он она оно объясни пожалуйста такое".split()
)


def tokens(text: str) -> list[str]:
    return STEMMER.stemWords(
        [w for w in re.findall(r"[\w]+", text.lower().replace("ё", "е")) if len(w) > 1 and w not in STOP]
    )


def retrieve(session, course_id: str, version: str, question: str, lesson_id: str | None, limit=5):
    chunks = list(
        session.scalars(
            select(Chunk).where(
                Chunk.course_id == course_id, Chunk.version == version, Chunk.active.is_(True)
            )
        )
    )
    query = set(tokens(question))
    if not chunks or not query:
        return []
    documents = [Counter(tokens(c.title + " " + c.text)) for c in chunks]
    frequency = Counter(t for doc in documents for t in doc)
    average = sum(sum(d.values()) for d in documents) / len(documents)
    scored = []
    for chunk, document in zip(chunks, documents):
        length = sum(document.values())
        score = 0.0
        for term in query:
            tf = document[term]
            if tf:
                idf = math.log(1 + (len(chunks) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / max(average, 1)))
        if score > 0:
            score *= 1.15 if chunk.lesson_id == lesson_id else 1
            scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [c for _, c in scored[:limit]]


def source(chunk: Chunk) -> dict:
    return {
        "chunk_id": chunk.id,
        "course_id": chunk.course_id,
        "lesson_id": chunk.lesson_id,
        "lesson_title": chunk.title,
        "materials_version": chunk.version,
        "excerpt": chunk.text,
        "source_url": chunk.source_url,
        "source_file": chunk.source_file,
        "start_ms": chunk.start_ms,
        "end_ms": chunk.end_ms,
        "link_status": "provided_unchecked" if chunk.source_url else "missing",
    }


def recommend(chunks: list[Chunk]) -> list[dict]:
    seen = set()
    result = []
    for chunk in chunks:
        if chunk.lesson_id in seen:
            continue
        seen.add(chunk.lesson_id)
        result.append(
            {
                "lesson_id": chunk.lesson_id,
                "title": chunk.title,
                "source_url": chunk.source_url,
                "reason": "В этом уроке найден материал по вопросу.",
                "next_step": "Прочитай указанный фрагмент и попробуй объяснить его своими словами.",
            }
        )
        if len(result) == 2:
            break
    return result
