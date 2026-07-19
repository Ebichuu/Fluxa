FROM node:20-slim AS web-build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.13-slim AS runtime
WORKDIR /app

COPY services/nasemby-core/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/nasemby-core/app /app/app
COPY vendor/mineradio-public /app/vendor/mineradio-public
COPY --from=web-build /app/dist /app/dist
RUN mkdir -p /app/data /app/db /app/upload

ENV PYTHONPATH=/app \
    APP_HOST=0.0.0.0 \
    APP_PORT=8987 \
    MCC_ENV=production \
    MCC_FRONTEND_DIST=/app/dist \
    MINERADIO_PUBLIC_DIR=/app/vendor/mineradio-public

EXPOSE 8987
CMD ["gunicorn", "--config", "app/gunicorn.conf.py", "app.main:app"]
