FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
# separate directory for runtime materials (key/known_hosts)
RUN useradd -m -u 1000 bot \
 && mkdir -p /app/runtime /home/bot/.ssh \
 && chown -R bot:bot /app /home/bot
USER bot

ENV PATH="/home/bot/.local/bin:${PATH}"
CMD ["python", "/app/bot.py"]
