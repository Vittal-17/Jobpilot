FROM python:3.12-alpine

WORKDIR /app
ARG APP_COMMIT_SHA=unknown
ENV APP_COMMIT_SHA=${APP_COMMIT_SHA}


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
