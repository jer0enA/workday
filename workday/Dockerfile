FROM python:3.14.10-alpine3.24

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tzdata: Supervisor passes the HA time zone via $TZ.
# apk upgrade pulls in Alpine security fixes released after the base image.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache \
    && apk add --no-cache tzdata \
    && adduser -D -H -u 1000 workday

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall -y pip

COPY app/ .

USER 1000:1000
EXPOSE 8099
CMD ["python", "main.py"]
