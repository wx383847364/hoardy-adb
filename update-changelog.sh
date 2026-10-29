#!/bin/sh -e
# 版本记录包含人工撰写的中文行为与验证说明，不再根据标签重建并覆盖正文。
cd "$(dirname "$0")"
test -s CHANGELOG.md
printf '%s\n' '版本记录使用中文维护，请在发布前同步 CHANGELOG.md、README.md 与打包配置。'
sed -n '/^## /p' CHANGELOG.md
