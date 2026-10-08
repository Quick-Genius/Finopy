FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
# Custodian + boto3 are the only cloud-facing code in the stack; versions are pinned after the first verified build.
COPY runner/requirements.lock ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip freeze > /srv/requirements.lock
COPY runner/server.py .
RUN useradd -m -u 10002 runner && mkdir /data && chown runner /data
USER runner
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
