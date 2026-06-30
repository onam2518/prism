#!/bin/bash
# 1024x1024 PNG → .icns 생성. 사용: ./make_icns.sh icon/prism-1024.png
set -e
SRC="${1:-icon/prism-1024.png}"
OUT="icon/prism.icns"
[ -f "$SRC" ] || { echo "원본 PNG 없음: $SRC (1024x1024 권장)"; exit 1; }

TMP="$(mktemp -d)/prism.iconset"
mkdir -p "$TMP"
for s in 16 32 64 128 256 512; do
  sips -z $s $s     "$SRC" --out "$TMP/icon_${s}x${s}.png"     >/dev/null
  sips -z $((s*2)) $((s*2)) "$SRC" --out "$TMP/icon_${s}x${s}@2x.png" >/dev/null
done
cp "$SRC" "$TMP/icon_512x512@2x.png"
iconutil -c icns "$TMP" -o "$OUT"
echo "생성: $OUT"
