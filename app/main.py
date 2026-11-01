import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.auth import Principal, admin, principal
from app.config import Settings
from app.db import database, initialize
from app.materials import active_payload, import_course
from app.models import Chunk, Course, Dialog, MaterialVersion, Message
from app.schemas import CourseImport, DialogCreate, MessageOut, Question
from app.search import retrieve, source


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    engine, sessions = database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        initialize(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Учебный AI-ассистент", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessions = sessions
    app.state.engine = engine

    def session_dependency():
        with sessions() as session:
            yield session

    def get_course(session, course_id, user):
        user.require_course(course_id)
        course = session.get(Course, course_id)
        if not course:
            raise HTTPException(404, "Курс не найден")
        return course

    def get_dialog(session, dialog_id, user, lock=False):
        stmt = select(Dialog).where(Dialog.id == dialog_id, Dialog.student_id == user.student_id)
        dialog = session.scalar(stmt.with_for_update() if lock else stmt)
        if not dialog:
            raise HTTPException(404, "Диалог не найден")
        user.require_course(dialog.course_id)
        return dialog

    @app.get("/health", tags=["system"])
    def health(session=Depends(session_dependency)):
        session.execute(text("SELECT 1"))
        return {"status": "ok", "auth_enabled": settings.auth_enabled, "llm_mode": settings.llm_mode}

    @app.post("/materials/import", tags=["materials"])
    def upload(data: CourseImport, user=Depends(admin), session=Depends(session_dependency)):
        try:
            result = import_course(session, data)
            session.commit()
            return result
        except (ValueError, IntegrityError) as exc:
            session.rollback()
            raise HTTPException(
                409, str(exc) if isinstance(exc, ValueError) else "Параллельный импорт: повтори запрос"
            ) from None

    @app.get("/courses", tags=["materials"])
    def courses(user: Principal = Depends(principal), session=Depends(session_dependency)):
        stmt = select(Course).order_by(Course.title)
        if not (user.development or user.admin):
            stmt = stmt.where(Course.id.in_(user.courses))
        return [
            {"id": c.id, "title": c.title, "materials_version": c.active_version}
            for c in session.scalars(stmt)
        ]

    @app.get("/courses/{course_id}", tags=["materials"])
    def course_details(course_id: str, user=Depends(principal), session=Depends(session_dependency)):
        course = get_course(session, course_id, user)
        payload = active_payload(session, course)
        return {
            "course": payload["course"],
            "materials_version": course.active_version,
            "blocks": payload["blocks"],
            "lessons": [
                {k: lesson.get(k) for k in ["id", "title", "block_id", "position", "active", "source_url"]}
                for lesson in payload["lessons"]
                if lesson.get("active", True)
            ],
        }

    @app.get("/courses/{course_id}/versions", tags=["materials"])
    def versions(course_id: str, user=Depends(admin), session=Depends(session_dependency)):
        course = get_course(session, course_id, user)
        return [
            {"version": v.version, "active": v.version == course.active_version, "created_at": v.created_at}
            for v in session.scalars(
                select(MaterialVersion)
                .where(MaterialVersion.course_id == course.id)
                .order_by(MaterialVersion.created_at)
            )
        ]

    @app.get("/courses/{course_id}/search", tags=["materials"])
    def search(
        course_id: str,
        q: str = Query(min_length=1, max_length=8000),
        user=Depends(principal),
        session=Depends(session_dependency),
    ):
        course = get_course(session, course_id, user)
        return [
            source(c) for c in retrieve(session, course.id, course.active_version, q, None, settings.top_k)
        ]

    @app.get("/courses/{course_id}/sources/{chunk_id}", tags=["materials"])
    def fragment(course_id: str, chunk_id: str, user=Depends(principal), session=Depends(session_dependency)):
        course = get_course(session, course_id, user)
        chunk = session.get(Chunk, chunk_id)
        if not chunk or chunk.course_id != course.id or not chunk.active:
            raise HTTPException(404, "Активный фрагмент не найден")
        return source(chunk)

    @app.post("/dialogs", status_code=201, tags=["dialogs"])
    def create_dialog(data: DialogCreate, user=Depends(principal), session=Depends(session_dependency)):
        course = get_course(session, data.course_id, user)
        if data.lesson_id and not any(
            lesson["id"] == data.lesson_id and lesson.get("active", True)
            for lesson in active_payload(session, course)["lessons"]
        ):
            raise HTTPException(422, "Урок не относится к активным материалам курса")
        dialog = Dialog(student_id=user.student_id, course_id=course.id, lesson_id=data.lesson_id)
        session.add(dialog)
        session.commit()
        return {"id": dialog.id, "course_id": dialog.course_id, "lesson_id": dialog.lesson_id}

    @app.get("/dialogs", tags=["dialogs"])
    def list_dialogs(
        user=Depends(principal),
        session=Depends(session_dependency),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        stmt = select(Dialog).where(Dialog.student_id == user.student_id)
        if not (user.development or user.admin):
            stmt = stmt.where(Dialog.course_id.in_(user.courses))
        return [
            {"id": d.id, "course_id": d.course_id, "lesson_id": d.lesson_id, "created_at": d.created_at}
            for d in session.scalars(stmt.order_by(Dialog.created_at.desc()).offset(offset).limit(limit))
        ]

    @app.post("/dialogs/{dialog_id}/messages", response_model=MessageOut, status_code=202, tags=["dialogs"])
    def ask(dialog_id: str, data: Question, user=Depends(principal), session=Depends(session_dependency)):
        get_dialog(session, dialog_id, user, lock=True)
        previous = session.scalar(
            select(Message).where(Message.dialog_id == dialog_id, Message.request_key == data.request_key)
        )
        if previous:
            if previous.question != data.text:
                raise HTTPException(409, "request_key уже использован для другого вопроса")
            return previous
        pending = session.scalar(
            select(Message.id).where(
                Message.dialog_id == dialog_id, Message.status.in_(["queued", "processing"])
            )
        )
        if pending:
            raise HTTPException(409, "Дождись завершения предыдущего вопроса")
        message = Message(dialog_id=dialog_id, question=data.text, request_key=data.request_key)
        session.add(message)
        session.commit()
        return message

    @app.get("/dialogs/{dialog_id}/messages", response_model=list[MessageOut], tags=["dialogs"])
    def history(
        dialog_id: str,
        user=Depends(principal),
        session=Depends(session_dependency),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        get_dialog(session, dialog_id, user)
        return list(
            session.scalars(
                select(Message)
                .where(Message.dialog_id == dialog_id)
                .order_by(Message.created_at, Message.id)
                .offset(offset)
                .limit(limit)
            )
        )

    @app.get("/messages/{message_id}", response_model=MessageOut, tags=["dialogs"])
    def message_status(message_id: str, user=Depends(principal), session=Depends(session_dependency)):
        message = session.get(Message, message_id)
        if not message:
            raise HTTPException(404, "Сообщение не найдено")
        get_dialog(session, message.dialog_id, user)
        return message

    @app.post("/messages/{message_id}/retry", response_model=MessageOut, status_code=202, tags=["dialogs"])
    def retry(message_id: str, user=Depends(principal), session=Depends(session_dependency)):
        message = session.get(Message, message_id)
        if not message:
            raise HTTPException(404, "Сообщение не найдено")
        get_dialog(session, message.dialog_id, user, lock=True)
        session.refresh(message, with_for_update=True)
        if message.status != "failed":
            raise HTTPException(409, "Повтор доступен только для неудавшейся обработки")
        pending = session.scalar(
            select(Message.id).where(
                Message.dialog_id == message.dialog_id, Message.status.in_(["queued", "processing"])
            )
        )
        if pending:
            raise HTTPException(409, "В диалоге уже обрабатывается вопрос")
        message.status = "queued"
        message.attempts = 0
        message.error = None
        message.available_at = time.time()
        session.commit()
        return message

    return app


app = create_app()
