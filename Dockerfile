FROM python:3.12-slim
WORKDIR /service
COPY pyproject.toml ./
COPY app ./app
COPY tools ./tools
RUN pip install --no-cache-dir .
COPY prompts ./prompts
COPY data ./data
RUN useradd --uid 10001 --create-home assistant && chmod -R a+rX /service
USER assistant
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
