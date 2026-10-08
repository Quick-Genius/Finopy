FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY api/requirements.lock ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip freeze > /srv/requirements.lock
COPY api/app ./app
COPY api/tests ./tests
COPY db/migrations /db/migrations
RUN useradd -r -u 10001 sentinel
USER sentinel
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
