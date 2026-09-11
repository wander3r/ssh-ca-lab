FROM smallstep/step-ca:latest
# Official image already supports DOCKER_STEPCA_INIT_* env vars.
# We only add a tiny helper that exports CA pubkeys for peer containers
# that share the step-ca-data volume (they read directly from /home/step/certs).
USER root
COPY export-ready.sh /usr/local/bin/export-ready.sh
RUN chmod +x /usr/local/bin/export-ready.sh
USER step
