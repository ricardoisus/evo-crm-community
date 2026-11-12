FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG VCS_REF=local
LABEL org.opencontainers.image.source="https://github.com/ricardoisus/evo-crm-community" org.opencontainers.image.revision=$VCS_REF
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY vetorial_agent/ vetorial_agent/
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir --no-deps . && useradd --uid 10001 --no-create-home agent
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready',timeout=2)"
CMD ["uvicorn","vetorial_agent.app:production","--factory","--host","0.0.0.0","--port","8080","--no-access-log","--log-level","warning"]
