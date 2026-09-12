FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates openssh-client \
 && rm -rf /var/lib/apt/lists/*
COPY step /usr/local/bin/step
RUN chmod +x /usr/local/bin/step
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
COPY app.py .
COPY policy.json .
COPY templates/ templates/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV STEPPATH=/home/step-client
RUN mkdir -p /home/step-client /work && chmod 777 /work
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
