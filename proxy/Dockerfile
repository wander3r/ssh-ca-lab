FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx openssl curl \
 && rm -rf /var/lib/apt/lists/* \
 && openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
      -keyout /etc/nginx/tls.key -out /etc/nginx/tls.crt \
      -subj "/CN=ssh-ca-lab" \
 && chmod 600 /etc/nginx/tls.key \
 && mkdir -p /var/cache/nginx /tmp/nginx
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 8080 8443
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD curl -sf http://127.0.0.1:8080/healthz >/dev/null
CMD ["nginx", "-g", "daemon off;"]
