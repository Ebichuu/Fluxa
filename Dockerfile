FROM node:22-slim AS web-build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.13-slim AS runtime
ARG FLUXA_BUILD_REVISION
WORKDIR /app

COPY services/fluxa-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/fluxa-api/app /app/app
COPY --from=web-build /app/dist /app/dist
RUN mkdir -p /app/data /app/db

ENV PYTHONPATH=/app \
    APP_HOST=0.0.0.0 \
    APP_PORT=8987 \
    MCC_ENV=production \
    FLUXA_BUILD_REVISION=${FLUXA_BUILD_REVISION} \
    FLUXA_FRONTEND_DIST=/app/dist

EXPOSE 8987
CMD ["gunicorn", "--config", "app/gunicorn.conf.py", "app.main:app"]
