FROM python:3.14-slim-bookworm

# create working directory (owned by non-root user later)
WORKDIR /webhook

# copy and install python dependencies first to leverage layer caching
COPY app/requirements.txt /webhook/
RUN pip install --no-cache-dir --upgrade -r /webhook/requirements.txt

# copy application sources
COPY app/main.py /webhook/
COPY app/config.py /webhook/
# create a dedicated non-root user and prepare filesystem
RUN groupadd -r webhook \
    && useradd -r -g webhook webhook \
    && mkdir -p /etc/webhook/certs \
    && chown -R webhook:webhook /webhook /etc/webhook

# switch to non-root user
USER webhook

# ensure python output is unbuffered (helpful for container logs)
ENV PYTHONUNBUFFERED=1

# run the application; main.py will itself choose appropriate ports/ssl
CMD ["python", "main.py"]

