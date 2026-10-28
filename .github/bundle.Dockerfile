# 部署层打包镜像。
#
# 这不是应用程序，而是「部署层」在某个版本上的不可变快照，
# 目的是让每个发布标签都对应一个可以拉取的容器包。
# 里面不含运行时要拉取的应用镜像的任何代码。

FROM alpine:3.20

LABEL org.opencontainers.image.title="My Quant Lab deployment bundle"
LABEL org.opencontainers.image.description="docker-compose.yml, configuration template, and operating notes for a single-NAS quant stack"
LABEL org.opencontainers.image.source="https://github.com/bobvane/My-Quant-Lab"
LABEL org.opencontainers.image.licenses="NOASSERTION"

WORKDIR /bundle

COPY docker-compose.yml .env.example VERSION AGENTS.md README.md README_CN.md NOTICE.md ./
COPY docs ./docs
COPY scripts ./scripts

CMD ["ls", "-la", "/bundle"]
