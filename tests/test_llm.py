import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import ProviderError, Tutor
from app.models import Chunk


def test_provider_request_and_citation_validation(monkeypatch):
    settings = Settings(llm_mode="api", llm_api_key="test", llm_model="test", _env_file=None)
    tutor = Tutor(settings)
    chunk = Chunk(
        id="a" * 64, course_id="c", lesson_id="l", title="Python", text="append", version="v1", position=1
    )
    seen = []
    content = {"answer": "Используй append [" + chunk.id + "]", "source_ids": [chunk.id]}

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    answer = asyncio.run(
        tutor.answer("не понял", [{"question": "списки", "answer": "прошлый ответ"}], [chunk])
    )
    assert answer.source_ids == [chunk.id]
    assert "прошлый ответ" in seen[0]["messages"][1]["content"]
    content["source_ids"] = ["invented"]
    with pytest.raises(ProviderError):
        asyncio.run(tutor.answer("вопрос", [], [chunk]))


def test_dump_unescape():
    from tools.export_dump import unescape

    assert unescape(r"\N") is None
    assert unescape(r"строка\nтаб\t\\") == "строка\nтаб\t\\"
