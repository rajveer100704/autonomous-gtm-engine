FROM python:3.11-slim

WORKDIR /app

COPY gtm_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gtm_engine ./gtm_engine

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "gtm_engine.api:app", "--host", "0.0.0.0", "--port", "8000"]
