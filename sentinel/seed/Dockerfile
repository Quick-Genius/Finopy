FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "boto3>=1.35,<2" "psycopg[binary]>=3.2,<4"
WORKDIR /seed
COPY seed/seed.py .
CMD ["python", "seed.py"]
