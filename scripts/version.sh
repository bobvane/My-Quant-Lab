#!/bin/bash
# My Quant Lab Versioning and Release Script
# 版本从 v0.0.1 开始，每到 10 进位 (v0.0.10 -> v0.1.0, v0.1.10 -> v0.2.0, etc.)

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="${PROJECT_ROOT}/version.txt"

# 获取当前版本
current_version() {
    if [ -f "$VERSION_FILE" ]; then
        cat "$VERSION_FILE"
    else
        echo "v0.0.1"
    fi
}

# 版本号加一
semver_increment() {
    local version=$1
    local major=$(echo "$version" | sed -E 's/^v([0-9]+)\.[0-9]+\.[0-9]+$/\1/')
    local minor=$(echo "$version" | sed -E 's/^v[0-9]+\.([0-9]+)\.[0-9]+$/\1/')
    local patch=$(echo "$version" | sed -E 's/^v[0-9]+\.[0-9]+\.([0-9]+)$/\1/')

    # 进位逻辑
    if [ "$patch" -lt 10 ]; then
        patch=$((patch + 1))
    elif [ "$minor" -lt 10 ]; then
        minor=$((minor + 1))
        patch=0
    else
        major=$((major + 1))
        minor=0
        patch=0
    fi

    echo "v${major}.${minor}.${patch}"
}

# 创建新的版本
create_version() {
    local current=$(current_version)
    local new_version=$(semver_increment "$current")

    # 更新版本文件
    echo "$new_version" > "$VERSION_FILE"

    # 提交版本变更
    git add "$VERSION_FILE"
    git commit -m "build: bump version to $new_version"

    echo "版本已更新到: $new_version"
}

# 生成发布目录
generate_release_package() {
    local version=$(current_version)
    local release_dir="release/${version}"
    local package_dir="dist/my-quant-lab-${version}"

    echo "生成发布包: $version"

    # 创建目录
    mkdir -p "$release_dir"
    mkdir -p "$package_dir"

    # 复制关键文件
    cp -r README.md "README_${version}.md"
    cp -r "My_Quant_Lab_Development_Docs" "$release_dir/"

    # 创建 Dockerfile
    cat > "${package_dir}/Dockerfile" <<EOF
FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 创建应用用户
RUN useradd -m -u 1000 quantuser
USER quantuser

# 复制应用代码
copy . /app

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 创建数据目录
RUN mkdir -p /app/data /app/logs

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
EOF

    # 创建 requirements.txt
    cat > "${package_dir}/requirements.txt" <<EOF
fastapi==0.104.1
sqlalchemy==2.0.23
alembic==1.12.0
python-multipart==0.0.21
pydantic==2.4.2
uvicorn[standard]==0.24.0
numpy==1.26.2
pandas==2.1.4
psycopg2-binary==2.9.9
redis==5.0.1
celery==5.3.4
python-dotenv==1.0.0
requests==2.31.0
pyyaml==6.0
EOF

    # 创建docker-compose.yml
    cat > "${package_dir}/docker-compose.yml" <<EOF
dversion: '3.8'

services:
  quantlab-api:
    build: ./dist/my-quant-lab-${version}/
    ports:
      - "8080:8080"
    environment:
      - DATABASE_URL=postgresql://quant_user:${DB_PASSWORD}@postgres:5432/quant_lab
      - REDIS_URL=redis://redis:6379
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  quantlab-worker:
    build: ./dist/my-quant-lab-${version}/
    command: celery -A quantlab worker -l info
    environment:
      - DATABASE_URL=postgresql://quant_user:${DB_PASSWORD}@postgres:5432/quant_lab
      - REDIS_URL=redis://redis:6379
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  quantlab-postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: quant_lab
      POSTGRES_USER: quant_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      PGDATA: /var/lib/postgresql/data
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - backend
    restart: unless-stopped

  quantlab-redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    networks:
      - backend
    restart: unless-stopped

  quantlab-web:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./dist/my-quant-lab-${version}/nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - quantlab-api
    networks:
      - frontend
      - backend
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:

networks:
  frontend:
    driver: bridge
  backend:
    driver: bridge
EOF

    # 创建 nginx 配置
    mkdir -p "${package_dir}/nginx"
    cat > "${package_dir}/nginx/nginx.conf" <<EOF
events {
    worker_connections 1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;

    server {
        listen       80;
        server_name  localhost;

        location / {
            proxy_pass http://quantlab-api:8080;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        location /api/ {
            proxy_pass http://quantlab-api:8080/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
}
EOF

    # 创建 documentation
    mkdir -p "${package_dir}/docs"
    cp -r "$PROJECT_ROOT/My_Quant_Lab_Development_Docs/00_README.md" "${package_dir}/docs/README.md"
    cp -r "$PROJECT_ROOT/My_Quant_Lab_Development_Docs/01_PRODUCT_SPEC.md" "${package_dir}/docs/"
    cp -r "$PROJECT_ROOT/My_Quant_Lab_Development_Docs/02_ARCHITECTURE.md" "${package_dir}/docs/"

    echo "发布包已生成: ${package_dir}"
    echo "请运行以下命令进行测试:" 
    echo "cd ${package_dir}"
    echo "docker build -t my-quant-lab:${version} ."
    echo "docker compose up -d"
    echo ""
    echo "访问地址:" 
    echo "- Web UI: http://localhost"
    echo "- API 健康检查: http://localhost:8080/api/v1/health"
}

# 显示版本信息
show_version() {
    echo "当前版本: $(current_version)"
}

# 主菜单
case "$1" in
    "create")
        create_version
        ;;
    "release")
        generate_release_package
        ;;
    "show")
        show_version
        ;;
    "*")
        echo "用法: $0 {create|release|show}"
        echo "  create: 创建新版本"
        echo "  release: 生成发布包"
        echo "  show: 显示当前版本"
        exit 1
        ;;
esac
