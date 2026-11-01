import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Chunk, Course, MaterialVersion
from app.schemas import CourseImport


def split_text(text: str, size: int = 1600, overlap: int = 200):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        value = text[start:end].strip()
        if value:
            yield value
        if end == len(text):
            break
        start = end - overlap


def import_course(session: Session, data: CourseImport) -> dict:
    payload = data.model_dump(mode="json")
    # Export timestamp/quality metadata do not change instructional content.
    canonical = {k: v for k, v in payload.items() if k not in {"exported_at", "quality"}}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    course = session.scalar(select(Course).where(Course.id == data.course.id).with_for_update())
    if not course:
        course = Course(id=data.course.id, title=data.course.title, active_version=data.materials_version)
        session.add(course)
        session.flush()
    old = session.scalar(
        select(MaterialVersion).where(
            MaterialVersion.course_id == course.id, MaterialVersion.version == data.materials_version
        )
    )
    if old and old.digest != digest:
        raise ValueError("Эта версия уже существует с другим содержимым; укажи новую materials_version")
    if not old:
        session.add(
            MaterialVersion(
                course_id=course.id, version=data.materials_version, digest=digest, payload=payload
            )
        )
        block_order = {str(b.id): b.position for b in data.blocks}
        lessons = sorted(
            data.lessons, key=lambda lesson: (block_order[str(lesson.block_id)], lesson.position, lesson.id)
        )
        for position, lesson in enumerate(lessons):
            if not lesson.active:
                continue
            parts = [(s.text, s.start_ms, s.end_ms) for s in lesson.segments]
            if not parts and lesson.text and lesson.text.strip():
                parts = [(lesson.text, None, None)]
            index = 0
            for text, start_ms, end_ms in parts:
                for fragment in split_text(text):
                    key = json.dumps([course.id, data.materials_version, lesson.id, index])
                    session.add(
                        Chunk(
                            id=hashlib.sha256(key.encode()).hexdigest(),
                            course_id=course.id,
                            version=data.materials_version,
                            lesson_id=lesson.id,
                            title=lesson.title,
                            text=fragment,
                            position=position,
                            source_url=str(lesson.source_url) if lesson.source_url else None,
                            source_file=lesson.source_file,
                            start_ms=start_ms,
                            end_ms=end_ms,
                            active=True,
                        )
                    )
                    index += 1
    session.flush()
    session.execute(update(Chunk).where(Chunk.course_id == course.id).values(active=False))
    session.execute(
        update(Chunk)
        .where(Chunk.course_id == course.id, Chunk.version == data.materials_version)
        .values(active=True)
    )
    course.active_version = data.materials_version
    course.title = data.course.title
    missing = [lesson.id for lesson in data.lessons if lesson.active and not (lesson.text or lesson.segments)]
    return {
        "course_id": course.id,
        "materials_version": course.active_version,
        "already_present": old is not None,
        "lessons": len(data.lessons),
        "lessons_without_text": missing,
        "lessons_without_url": [
            lesson.id for lesson in data.lessons if lesson.active and not lesson.source_url
        ],
    }


def active_payload(session: Session, course: Course) -> dict:
    return session.scalar(
        select(MaterialVersion).where(
            MaterialVersion.course_id == course.id, MaterialVersion.version == course.active_version
        )
    ).payload
