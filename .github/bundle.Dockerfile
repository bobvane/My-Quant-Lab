# Deployment bundle image.
#
# This is not the application. It is a versioned, immutable snapshot of this
# deployment layer so that every release tag has a matching container package.
# It contains no code from the application images this stack pulls at runtime.

FROM alpine:3.20

LABEL org.opencontainers.image.title="My Quant Lab deployment bundle"
LABEL org.opencontainers.image.description="docker-compose.yml, configuration template, and operating notes for a single-NAS quant stack"
LABEL org.opencontainers.image.source="https://github.com/bobvane/My-Quant-Lab"
LABEL org.opencontainers.image.licenses="NOASSERTION"

WORKDIR /bundle

COPY docker-compose.yml .env.example VERSION README.md README_CN.md NOTICE.md ./
COPY docs ./docs
COPY scripts ./scripts

CMD ["ls", "-la", "/bundle"]
