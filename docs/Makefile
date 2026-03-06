SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = source
BUILDDIR      = build
PORT          ?= 8000

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# 构建 HTML 并启动本地 HTTP 服务，浏览器打开 http://127.0.0.1:$(PORT)
serve: html
	@echo "文档已构建，请在浏览器打开: http://127.0.0.1:$(PORT)"
	cd $(BUILDDIR)/html && python3 -m http.server $(PORT)

.PHONY: help Makefile serve

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
