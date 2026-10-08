# CloudQuery's hub requires `cloudquery login` for every postgresql destination version we tried (v7.4.0, v8.9.0, v8.17.0),
# while the AWS source v22.19.2 downloads anonymously. To keep the demo account-free, the destination is built from the
# upstream tag (cloudquery/cloudquery is MPL-2.0) and run as a *local* plugin. Pins: see docs/PHASE0.md.
FROM golang:1.27 AS pgdest
RUN git clone --depth 1 --branch plugins-destination-postgresql-v8.17.0 https://github.com/cloudquery/cloudquery /src
WORKDIR /src/plugins/destination/postgresql
RUN CGO_ENABLED=0 go build -o /cq-destination-postgresql .

FROM ghcr.io/cloudquery/cloudquery:6.45.0 AS cq

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1"
COPY --from=cq /app/cloudquery /usr/local/bin/cloudquery
COPY --from=pgdest /cq-destination-postgresql /usr/local/bin/cq-destination-postgresql
WORKDIR /srv
COPY cq/server.py .
COPY cq/spec.yml /spec/spec.yml
RUN useradd -m -u 10003 cq
USER cq
EXPOSE 8080
CMD ["uvicorn", "--app-dir", "/srv", "server:app", "--host", "0.0.0.0", "--port", "8080"]
