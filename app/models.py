import time
import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    active_version: Mapped[str] = mapped_column(String(128))


class MaterialVersion(Base):
    __tablename__ = "material_versions"
    __table_args__ = (UniqueConstraint("course_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    version: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_course_version", "course_id", "version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"))
    version: Mapped[str] = mapped_column(String(128))
    lesson_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Dialog(Base):
    __tablename__ = "dialogs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    student_id: Mapped[str] = mapped_column(String(128), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"))
    lesson_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("dialog_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dialog_id: Mapped[str] = mapped_column(ForeignKey("dialogs.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(128))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    answer: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[float] = mapped_column(Float, default=time.time)
    lease_until: Mapped[float | None] = mapped_column(Float)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    materials_version: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
