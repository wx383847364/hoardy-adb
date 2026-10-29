#!/bin/sh -e
# 中文 README 由人工维护，只生成可选预览，禁止自动英文帮助覆盖源文档。
cd "$(dirname "$0")"
test -s README.md
if command -v pandoc >/dev/null 2>&1; then
    mkdir -p build/docs
    pandoc -V pagetitle='安卓应用搬家使用说明' -f markdown -t html README.md -o build/docs/README.html
    printf '%s\n' '中文 README 已保留，HTML 预览位于 build/docs/README.html。'
else
    printf '%s\n' '中文 README 已保留；未安装 pandoc，跳过 HTML 预览。'
fi
