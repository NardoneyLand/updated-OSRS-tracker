#!/usr/bin/env bash
# make_icns.sh — convert icon.png / icon.jpg / icon.jpeg into icon.icns
# using macOS built-ins (sips + iconutil). Source can be any size; ideally
# square and ≥ 1024×1024. Idempotent — only rebuilds if source is newer.
set -euo pipefail
cd "$(dirname "$0")"

# Find an icon source file
SRC=""
for candidate in icon.png icon.jpg icon.jpeg icon.PNG icon.JPG icon.JPEG; do
    if [ -f "$candidate" ]; then
        SRC="$candidate"
        break
    fi
done

OUT=icon.icns
SET=icon.iconset

if [ -z "$SRC" ]; then
    echo "No icon source found (looked for icon.png, icon.jpg, icon.jpeg) — skipping."
    exit 0
fi

if [ -f "$OUT" ] && [ "$OUT" -nt "$SRC" ]; then
    echo "$OUT is up to date."
    exit 0
fi

echo "==> Generating $OUT from $SRC..."

rm -rf "$SET"
mkdir -p "$SET"

# sips infers output format from the .png extension, so JPEG sources convert automatically.
sips -s format png -z 16 16     "$SRC" --out "$SET/icon_16x16.png"      >/dev/null
sips -s format png -z 32 32     "$SRC" --out "$SET/icon_16x16@2x.png"   >/dev/null
sips -s format png -z 32 32     "$SRC" --out "$SET/icon_32x32.png"      >/dev/null
sips -s format png -z 64 64     "$SRC" --out "$SET/icon_32x32@2x.png"   >/dev/null
sips -s format png -z 128 128   "$SRC" --out "$SET/icon_128x128.png"    >/dev/null
sips -s format png -z 256 256   "$SRC" --out "$SET/icon_128x128@2x.png" >/dev/null
sips -s format png -z 256 256   "$SRC" --out "$SET/icon_256x256.png"    >/dev/null
sips -s format png -z 512 512   "$SRC" --out "$SET/icon_256x256@2x.png" >/dev/null
sips -s format png -z 512 512   "$SRC" --out "$SET/icon_512x512.png"    >/dev/null
sips -s format png -z 1024 1024 "$SRC" --out "$SET/icon_512x512@2x.png" >/dev/null

iconutil -c icns "$SET" -o "$OUT"
rm -rf "$SET"

echo "    Wrote $OUT"
