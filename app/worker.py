import asyncio
import logging
import signal
import time

from sqlalchemy import and_, or_, select

from app.config import Settings
from app.db import database
from app.llm import ProviderError, Tutor
from app.materials import active_payload
from app.models import Course, Dialog, Message, uid
from app.search import recommend, retrieve, source

logger = logging.getLogger(__name__)


def claim_job(sessions, settings):
    with sessions.begin() as session:
        job = session.scalar(
            select(Message)
            .where(
                or_(
                    and_(Message.status == "queued", Message.available_at <= time.time()),
                    and_(Message.status == "processing", Message.lease_until < time.time()),
                )
            )
            .order_by(Message.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        if job.attempts >= settings.max_attempts:
            job.status = "failed"
            job.error = "Лимит попыток исчерпан. Можно запустить повторную обработку."
            job.lease_until = None
            return None
        job.status = "processing"
        job.attempts += 1
        job.claim_token = uid()
        job.lease_until = time.time() + settings.lease_seconds
        job.error = None
        return job.id, job.claim_token


async def generate(tutor, settings, question, history, chunks, description):
    return await asyncio.wait_for(
        tutor.answer(question, history, chunks, description), timeout=settings.llm_timeout_seconds
    )


def process_one(sessions, settings, tutor=None):
    claimed = claim_job(sessions, settings)
    if not claimed:
        return False
    job_id, token = claimed
    tutor = tutor or Tutor(settings)
    try:
        with sessions.begin() as session:
            job = session.get(Message, job_id)
            dialog = session.get(Dialog, job.dialog_id)
            course = session.get(Course, dialog.course_id)
            version = course.active_version
            payload = active_payload(session, course)
            previous = list(
                session.scalars(
                    select(Message)
                    .where(
                        Message.dialog_id == dialog.id,
                        Message.status == "completed",
                        Message.created_at < job.created_at,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(8)
                )
            )
            history = [
                {"question": m.question, "answer": (m.answer or "")[:5000]} for m in reversed(previous)
            ]
            query = job.question
            # Elliptical follow-ups inherit the prior topic, but do not mix courses or users.
            if previous and (
                len(query.split()) < 5
                or any(
                    marker in query.lower()
                    for marker in ["не понял", "не поняла", "другой пример", "попроще"]
                )
            ):
                query = previous[0].question + " " + query
            chunks = retrieve(session, course.id, version, query, dialog.lesson_id, settings.top_k)
            job.materials_version = version
            job.model = tutor.model
            job.prompt_version = tutor.prompt_version
            question = job.question
        result = asyncio.run(
            generate(tutor, settings, question, history, chunks, payload["course"]["description"])
        )
        with sessions.begin() as session:
            job = session.scalar(select(Message).where(Message.id == job_id).with_for_update())
            if job.claim_token != token or job.status != "processing":
                return True
            # An update while the provider was answering invalidates this result.
            current_course = session.scalar(
                select(Course).where(Course.id == dialog.course_id).with_for_update()
            )
            if current_course.active_version != version:
                job.status = "queued"
                job.available_at = time.time()
                job.lease_until = None
                job.error = "Материалы обновились; повторяем поиск по новой версии"
                return True
            selected = [c for c in chunks if c.id in result.source_ids]
            job.answer = result.answer
            job.sources = [source(c) for c in selected]
            job.recommendations = recommend(selected)
            job.status = "completed"
            job.error = None
            job.lease_until = None
    except Exception as exc:
        logger.warning("Job %s failed: %s", job_id, type(exc).__name__)
        with sessions.begin() as session:
            job = session.scalar(select(Message).where(Message.id == job_id).with_for_update())
            if job.claim_token == token and job.status == "processing":
                job.error = str(exc) if isinstance(exc, ProviderError) else "Не удалось обработать вопрос"
                job.status = "failed" if job.attempts >= settings.max_attempts else "queued"
                job.available_at = time.time() + settings.retry_delay_seconds * (2 ** (job.attempts - 1))
                job.lease_until = None
    return True


def main():
    settings = Settings()
    engine, sessions = database(settings.database_url)
    tutor = Tutor(settings)
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO)
    try:
        while not stopped:
            try:
                worked = process_one(sessions, settings, tutor)
            except Exception as exc:
                logger.warning("Worker database unavailable: %s", type(exc).__name__)
                worked = False
            if not worked:
                time.sleep(settings.poll_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
