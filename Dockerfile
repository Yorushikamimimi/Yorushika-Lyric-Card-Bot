# 使用官方 Python 轻量级镜像
FROM python:3.10-slim

# 1. 设置工作目录
WORKDIR /app

# 2. 安装系统依赖 (Playwright 需要的一些库 + 字体支持)
RUN apt-get update && apt-get install -y \
    wget \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 3. 复制依赖文件并安装 Python 库
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 安装 Playwright 浏览器内核 (及依赖)
RUN playwright install chromium
RUN playwright install-deps

# 5. 复制项目代码和素材
COPY . .

# 6. 暴露端口
EXPOSE 8000

# 7. 启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]