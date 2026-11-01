import hashlib
import json
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from app.search import source


class ProviderError(Exception):
    pass


class GeneratedAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=20000)
    source_ids: list[str] = Field(max_length=10)


class Tutor:
    def __init__(self, settings):
        self.settings = settings
        self.prompt = Path(settings.prompt_path).read_text()
        self.prompt_version = hashlib.sha256(self.prompt.encode()).hexdigest()
        self.model = settings.llm_model if settings.llm_mode == "api" else "extractive-v1"

    async def answer(self, question, history, chunks, description=""):
        if not chunks:
            return GeneratedAnswer(
                answer="В материалах курса не нашёл подходящего фрагмента. "
                "Уточни тему или название урока; при необходимости обратись к преподавателю.",
                source_ids=[],
            )
        if self.settings.llm_mode == "extractive":
            selected = chunks[:2]
            answer = "Режим поиска без LLM. Найденные выдержки из уроков:\n\n"
            answer += "\n\n".join(f"«{c.text}» [{c.id}]" for c in selected)
            return GeneratedAnswer(answer=answer, source_ids=[c.id for c in selected])
        context = {
            "course_description": description[:3000],
            "history": history,
            "question": question,
            "materials": [source(c) for c in chunks],
        }
        try:
            # One overall deadline, in addition to per-operation HTTP timeouts, is enforced by the worker.
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(
                    self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json={
                        "model": self.model,
                        "temperature": 0.3,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": self.prompt},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                        ],
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                result = GeneratedAnswer.model_validate_json(content)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Provider bodies may contain secrets or sensitive prompt content; never persist them.
            raise ProviderError("Провайдер недоступен или вернул некорректный ответ") from exc
        allowed = {c.id for c in chunks}
        if not set(result.source_ids).issubset(allowed):
            raise ProviderError("Модель вернула неизвестный источник")
        import re

        cited = set(re.findall(r"\[([a-f0-9]{64})\]", result.answer))
        if cited != set(result.source_ids):
            raise ProviderError("Ссылки в ответе не совпадают со списком источников")
        # Links must come from server-side source records, never from generated prose.
        if re.search(r"https?://", result.answer):
            raise ProviderError("Модель вернула непроверенную ссылку в тексте")
        return result
