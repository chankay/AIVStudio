# ---- 构建阶段：装依赖到独立目录，运行阶段原样拷贝，镜像更小 ----
FROM python:3.12-slim AS deps
WORKDIR /install
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install-pkg -r requirements.txt

# ---- 运行阶段 ----
FROM python:3.12-slim
WORKDIR /app

# ffmpeg：TTS 配音混流 + 正片拼接依赖（apt 版体积可控，静态版反而拖大镜像）
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 依赖
COPY --from=deps /install-pkg /usr/local

# 代码
COPY server.py auth.py db.py media.py providers.py ./
COPY web ./web

# 数据目录（运行时挂卷持久化）
RUN mkdir -p /app/data

ENV MODE=mock \
    PYTHONUNBUFFERED=1

EXPOSE 8020

# 直接用 uvicorn 起，绑 0.0.0.0 才能被容器外访问；
# 不用 `python server.py` 是因为其中硬编码了 127.0.0.1
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8020"]
