#!/bin/sh
# Tailwind 사전 빌드: Play CDN 런타임(397KB+브라우저 JIT) 대체 · 산출물 = prism/vendor/tw.css
# 마크업(page.py)·앱 JS(app.js)에 유틸 클래스를 추가/변경했다면 재실행 후 tw.css 를 커밋한다.
set -e
cd "$(dirname "$0")/.."
npx -y tailwindcss@3.4.17 -c scripts/tailwind.config.js -i scripts/tailwind.in.css -o prism/vendor/tw.css --minify
wc -c prism/vendor/tw.css
