#!/bin/bash
# Prism.app → 꾸민 DMG (nunchi 스타일: 배경 + Applications 드래그 + 볼륨 아이콘)
# 사전: brew install create-dmg, dist/Prism.app 존재
set -e
cd "$(dirname "$0")/.."

APP_NAME="${1:-Prism}"
APP="dist/${APP_NAME}.app"
VER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || echo 0.1.0)"
OUT="dist/${APP_NAME// /-}-${VER}.dmg"
[ -d "$APP" ] || { echo "먼저 .app 을 빌드하세요 (BUILD.md 참고): $APP 없음"; exit 1; }
rm -f "$OUT"

ARGS=(
  --volname "${APP_NAME} ${VER}"
  --window-pos 200 120
  --window-size 600 400
  --icon-size 110
  --icon "${APP_NAME}.app" 150 190
  --app-drop-link 450 190
  --hide-extension "${APP_NAME}.app"
  --no-internet-enable
)
[ -f desktop/dmg/background.png ] && ARGS+=( --background "desktop/dmg/background.png" )
[ -f desktop/icon/prism.icns ]   && ARGS+=( --volicon "desktop/icon/prism.icns" )

create-dmg "${ARGS[@]}" "$OUT" "$APP"
echo "생성: $OUT"
