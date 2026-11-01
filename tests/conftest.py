import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.db", _env_file=None, max_attempts=2, retry_delay_seconds=0
    )


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def course():
    return {
        "schema_version": 1,
        "materials_version": "v1",
        "course": {"id": "course-a", "title": "Python"},
        "blocks": [{"id": 1, "title": "Основы", "position": 1}],
        "lessons": [
            {
                "id": "lesson-a",
                "block_id": 1,
                "position": 1,
                "title": "Списки Python",
                "text": "Список Python хранит упорядоченную коллекцию элементов. "
                "Метод append добавляет элемент в конец списка.",
            }
        ],
    }
