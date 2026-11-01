import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class CourseData(BaseModel):
    id: Identifier
    slug: str = ""
    title: str = Field(min_length=1)
    description: str = ""


class Block(BaseModel):
    id: int | str
    title: str
    position: int = Field(ge=0)


class Segment(BaseModel):
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def timestamps(self):
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms precedes start_ms")
        return self


class Lesson(BaseModel):
    id: Identifier
    block_id: int | str
    position: int = Field(ge=0)
    title: str
    type: str = "lecture"
    text: str | None = None
    summary: str | None = None
    content_kind: str | None = None
    source_url: HttpUrl | None = None
    source_file: str | None = None
    video_duration_ms: int | None = Field(default=None, ge=0)
    updated_at: str | None = None
    text_sha256: str | None = None
    active: bool = True
    segments: list[Segment] = Field(default_factory=list)

    @model_validator(mode="after")
    def checksum(self):
        if self.text and self.text_sha256:
            if hashlib.sha256(self.text.encode()).hexdigest() != self.text_sha256:
                raise ValueError("text_sha256 mismatch")
        if self.video_duration_ms is not None and any(
            s.end_ms > self.video_duration_ms for s in self.segments
        ):
            raise ValueError("Segment exceeds video duration")
        return self


class CourseImport(BaseModel):
    schema_version: Literal[1] = 1
    exported_at: str | None = None
    materials_version: Identifier
    course: CourseData
    blocks: list[Block]
    lessons: list[Lesson] = Field(max_length=10000)
    quality: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def references(self):
        blocks = {str(b.id) for b in self.blocks}
        if len(blocks) != len(self.blocks):
            raise ValueError("Duplicate block IDs")
        if len({lesson.id for lesson in self.lessons}) != len(self.lessons):
            raise ValueError("Duplicate lesson IDs")
        if any(str(lesson.block_id) not in blocks for lesson in self.lessons):
            raise ValueError("Unknown block_id")
        return self


class DialogCreate(BaseModel):
    course_id: Identifier
    lesson_id: Identifier | None = None


class Question(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    request_key: Identifier

    @model_validator(mode="after")
    def not_blank(self):
        if not self.text.strip():
            raise ValueError("Question is blank")
        return self


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dialog_id: str
    question: str
    status: str
    answer: str | None
    sources: list[dict]
    recommendations: list[dict]
    attempts: int
    error: str | None
    materials_version: str | None
    model: str | None
    prompt_version: str | None
    created_at: float
