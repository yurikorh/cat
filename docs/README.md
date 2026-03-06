# 文档

Sphinx 文档，支持 Markdown（MyST）与 reStructuredText。

## 本地查看

```bash
# 1. 安装文档依赖（在项目根目录）
pip install -e ".[docs]"

# 2. 构建并启动本地服务（在 docs 目录）
cd docs
make serve
```

浏览器打开 **http://127.0.0.1:8000** 即可查看。默认端口 8000，可指定：`make serve PORT=8080`。

## 仅构建不启动服务

```bash
cd docs
make html
# 静态文件在 docs/build/html/
```

用任意静态文件服务器打开 `build/html/index.html`，或 `python3 -m http.server 8000` 在 `build/html` 下启动。
