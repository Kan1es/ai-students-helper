import copy
import time

import jwt
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import ProviderError
from app.main import create_app
from app.models import Message
from app.worker import process_one


def setup_dialog(client, course):
    assert client.post("/materials/import", json=course).status_code == 200
    response = client.post("/dialogs", json={"course_id": course["course"]["id"]})
    assert response.status_code == 201
    return response.json()["id"]


def ask(client, dialog, key="q1", question="Как добавить элемент в список Python?"):
    response = client.post(f"/dialogs/{dialog}/messages", json={"text": question, "request_key": key})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_full_flow_and_isolation(client, course, settings):
    dialog = setup_dialog(client, course)
    mid = ask(client, dialog)
    assert ask(client, dialog) == mid
    assert (
        client.post(f"/dialogs/{dialog}/messages", json={"text": "другое", "request_key": "q2"}).status_code
        == 409
    )
    assert process_one(client.app.state.sessions, settings)
    message = client.get(f"/messages/{mid}").json()
    assert message["status"] == "completed"
    assert message["sources"][0]["course_id"] == "course-a"
    assert "append" in message["answer"]
    assert message["model"] == "extractive-v1"
    assert message["prompt_version"]
    assert client.get(f"/messages/{mid}", headers={"X-Student-ID": "stranger"}).status_code == 404
    assert client.get(f"/dialogs/{dialog}/messages", headers={"X-Student-ID": "stranger"}).status_code == 404
    assert len(client.get(f"/dialogs/{dialog}/messages").json()) == 1


def test_version_replacement_and_history(client, course, settings):
    dialog = setup_dialog(client, course)
    mid = ask(client, dialog)
    process_one(client.app.state.sessions, settings)
    old_sources = client.get(f"/messages/{mid}").json()["sources"]
    changed = copy.deepcopy(course)
    changed["lessons"][0]["text"] = "Новый урок посвящен исключительно регрессии."
    assert client.post("/materials/import", json=changed).status_code == 409
    changed["materials_version"] = "v2"
    assert client.post("/materials/import", json=changed).status_code == 200
    assert not client.get("/courses/course-a/search", params={"q": "append"}).json()
    assert client.get(f"/messages/{mid}").json()["sources"] == old_sources
    changed["materials_version"] = "v3"
    changed["lessons"][0]["active"] = False
    client.post("/materials/import", json=changed)
    assert not client.get("/courses/course-a/search", params={"q": "регрессия"}).json()


def test_cross_course_and_no_data(client, course, settings):
    dialog = setup_dialog(client, course)
    other = copy.deepcopy(course)
    other["course"]["id"] = "course-b"
    other["lessons"][0]["text"] = "Квантовый телепорт использует запутанность."
    client.post("/materials/import", json=other)
    mid = ask(client, dialog, question="Квантовый телепорт")
    process_one(client.app.state.sessions, settings)
    result = client.get(f"/messages/{mid}").json()
    assert result["status"] == "completed"
    assert not result["sources"]
    assert "Уточни" in result["answer"]
    assert client.post("/dialogs", json={"course_id": "course-a", "lesson_id": "unknown"}).status_code == 422


class BrokenTutor:
    model = "broken"
    prompt_version = "test"

    async def answer(self, *args):
        raise ProviderError("Временный сбой")


def test_failure_retry_and_lease_recovery(client, course, settings):
    dialog = setup_dialog(client, course)
    mid = ask(client, dialog)
    sessions = client.app.state.sessions
    process_one(sessions, settings, BrokenTutor())
    assert client.get(f"/messages/{mid}").json()["status"] == "queued"
    process_one(sessions, settings, BrokenTutor())
    assert client.get(f"/messages/{mid}").json()["status"] == "failed"
    assert client.post(f"/messages/{mid}/retry").status_code == 202
    with sessions.begin() as s:
        m = s.get(Message, mid)
        m.status = "processing"
        m.lease_until = time.time() - 1
    process_one(sessions, settings)
    assert client.get(f"/messages/{mid}").json()["status"] == "completed"


def test_validation(client, course):
    course["lessons"][0]["text_sha256"] = "wrong"
    assert client.post("/materials/import", json=course).status_code == 422
    course["lessons"][0].pop("text_sha256")
    course["lessons"][0]["block_id"] = 999
    assert client.post("/materials/import", json=course).status_code == 422


def test_authenticated_permissions(tmp_path, course):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/auth.db", auth_enabled=True, jwt_secret="a" * 40, _env_file=None
    )

    def header(subject, courses, role="student"):
        token = jwt.encode(
            {
                "sub": subject,
                "courses": courses,
                "role": role,
                "exp": time.time() + 600,
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    with TestClient(create_app(settings)) as c:
        admin = header("admin", [], "admin")
        student = header("one", ["course-a"])
        assert c.get("/courses").status_code == 401
        assert c.post("/materials/import", json=course, headers=student).status_code == 403
        assert c.post("/materials/import", json=course, headers=admin).status_code == 200
        assert c.get("/courses/course-a/search?q=Python", headers=header("two", [])).status_code == 403
        dialog = c.post("/dialogs", json={"course_id": "course-a"}, headers=student).json()["id"]
        assert c.get(f"/dialogs/{dialog}/messages", headers=header("two", ["course-a"])).status_code == 404
        assert c.get("/courses", headers=header("two", [])).json() == []


def test_update_during_generation_discards_stale_answer(client, course, settings):
    from app.llm import GeneratedAnswer
    from app.materials import import_course
    from app.schemas import CourseImport

    dialog = setup_dialog(client, course)
    mid = ask(client, dialog)
    sessions = client.app.state.sessions
    updated = copy.deepcopy(course)
    updated["materials_version"] = "v2"
    updated["lessons"][0]["text"] = "Список Python содержит элементы новой версии."

    class UpdatingTutor:
        model = "test"
        prompt_version = "test"

        async def answer(self, question, history, chunks, description):
            with sessions.begin() as session:
                import_course(session, CourseImport.model_validate(updated))
            return GeneratedAnswer(answer="Устаревший ответ", source_ids=[chunks[0].id])

    process_one(sessions, settings, UpdatingTutor())
    assert client.get(f"/messages/{mid}").json()["status"] == "queued"
    process_one(sessions, settings)
    result = client.get(f"/messages/{mid}").json()
    assert result["materials_version"] == "v2"
    assert result["answer"] != "Устаревший ответ"
    assert all(s["materials_version"] == "v2" for s in result["sources"])


def test_overall_provider_timeout_preserves_question(client, course, settings):
    import asyncio

    settings.llm_timeout_seconds = 0.01
    dialog = setup_dialog(client, course)
    mid = ask(client, dialog)

    class SlowTutor:
        model = "slow"
        prompt_version = "test"

        async def answer(self, *args):
            await asyncio.sleep(10)

    process_one(client.app.state.sessions, settings, SlowTutor())
    result = client.get(f"/messages/{mid}").json()
    assert result["status"] == "queued"
    assert result["question"] == "Как добавить элемент в список Python?"
    assert result["attempts"] == 1


def test_timestamps_and_summary_not_indexed(client, course):
    lesson = course["lessons"][0]
    lesson["text"] = ""
    lesson["summary"] = "Уникальныйсекрет"
    lesson["segments"] = [{"text": "Список хранит элементы", "start_ms": 100, "end_ms": 800}]
    lesson["video_duration_ms"] = 900
    assert client.post("/materials/import", json=course).status_code == 200
    found = client.get("/courses/course-a/search?q=список").json()
    assert found[0]["start_ms"] == 100
    assert found[0]["end_ms"] == 800
    assert client.get("/courses/course-a/search?q=Уникальныйсекрет").json() == []
