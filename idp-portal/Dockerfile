FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates openssh-client \
 && rm -rf /var/lib/apt/lists/*

# Install step CLI (same major as step-ca image family)
ARG STEP_VERSION=0.28.3
RUN curl -fsSL -o /tmp/step.tgz \
      "https://dl.smallstep.com/gh-release/cli/gh-release-header/v${STEP_VERSION}/step_linux_${STEP_VERSION}_amd64.tar.gz" \
 && tar -xzf /tmp/step.tgz -C /tmp \
 && mv /tmp/step_${STEP_VERSION}/bin/step /usr/local/bin/step \
 && chmod +x /usr/local/bin/step \
 && rm -rf /tmp/step.tgz /tmp/step_${STEP_VERSION} \
 && step version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates/ templates/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV STEPPATH=/home/step-client
RUN mkdir -p /home/step-client /work && chmod 777 /work

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
