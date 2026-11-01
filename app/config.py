from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./assistant.db"
    llm_mode: Literal["extractive", "api"] = "extractive"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 45
    max_attempts: int = 3
    retry_delay_seconds: float = 3
    lease_seconds: float = 180
    poll_seconds: float = 0.5
    auth_enabled: bool = False
    jwt_secret: str = ""
    jwt_issuer: str = "learning-platform"
    jwt_audience: str = "course-assistant"
    prompt_path: str = "prompts/tutor.txt"
    top_k: int = 5

    @model_validator(mode="after")
    def valid_configuration(self):
        if self.auth_enabled and len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        if self.llm_mode == "api" and not (self.llm_api_key and self.llm_model):
            raise ValueError("Set LLM_API_KEY and LLM_MODEL for api mode")
        if self.max_attempts < 1 or self.llm_timeout_seconds <= 0:
            raise ValueError("Attempts and timeout must be positive")
        if self.lease_seconds <= self.llm_timeout_seconds + 30:
            raise ValueError("Lease must exceed LLM timeout by at least 30 seconds")
        if not 1 <= self.top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        return self
