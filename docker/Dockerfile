# ============================================
# Vigilus - Multi-stage Docker Build
# ============================================

# Stage 1: Build Frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python Runtime
FROM python:3.11-slim AS runtime

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY backend/pyproject.toml ./backend/
RUN pip install --no-cache-dir -e "./backend[prod]" 2>/dev/null || pip install --no-cache-dir -e "./backend"

# Copy backend source
COPY backend/ ./backend/

# Install the package
RUN pip install --no-cache-dir -e ./backend

# Copy built frontend
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Create data directory
RUN mkdir -p /app/data

# Environment defaults
ENV VIGILUS_DATA_DIR=/app/data \
    VIGILUS_DATABASE_URL=sqlite+aiosqlite:///app/data/vigilus.db \
    VIGILUS_HOST=0.0.0.0 \
    VIGILUS_PORT=8000

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/api/health'); r.raise_for_status()" || exit 1

# Run
CMD ["vigilus", "start"]
