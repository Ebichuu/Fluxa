FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
RUN mkdir -p /app/data /app/db /app/upload

ENV PYTHONPATH=/app
ENV APP_PORT=12388
EXPOSE 12388

CMD ["gunicorn", "--config", "app/gunicorn.conf.py", "app.main:app"]
