#!/usr/bin/env bash
# release.sh — build the .app, package it as a .dmg + .zip, and (optionally)
# publish to GitHub Releases.
#
# Prerequisites (one-time setup):
#   brew install create-dmg gh
#   gh auth login
#
# Usage:
#   ./release.sh                  # build + package only
#   ./release.sh v1.0.0           # also publish that tag to GitHub Releases
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-}"
APP_NAME="OSRS Tracker"
APP_PATH="dist/${APP_NAME}.app"
DMG_PATH="dist/${APP_NAME}.dmg"
ZIP_PATH="dist/${APP_NAME}.zip"

# 1. Build the .app
echo "==> Building .app via build.sh..."
./build.sh

if [ ! -d "$APP_PATH" ]; then
    echo "!! Expected $APP_PATH after build but it was missing." >&2
    exit 1
fi

# 2. Create a .zip (always — broadest compatibility)
echo "==> Creating ${ZIP_PATH}..."
rm -f "$ZIP_PATH"
( cd dist && zip -qry "${APP_NAME}.zip" "${APP_NAME}.app" )

# 3. Create a .dmg if create-dmg is installed
if command -v create-dmg >/dev/null 2>&1; then
    echo "==> Creating ${DMG_PATH}..."
    rm -f "$DMG_PATH"
    create-dmg \
        --volname "$APP_NAME" \
        --window-size 540 320 \
        --icon-size 100 \
        --icon "${APP_NAME}.app" 130 150 \
        --hide-extension "${APP_NAME}.app" \
        --app-drop-link 410 150 \
        --no-internet-enable \
        "$DMG_PATH" \
        "$APP_PATH"
else
    echo "!! create-dmg not installed — skipping DMG."
    echo "   Install with: brew install create-dmg"
fi

echo ""
echo "==> Artifacts:"
ls -lh "$ZIP_PATH" 2>/dev/null || true
ls -lh "$DMG_PATH" 2>/dev/null || true

# 4. Publish to GitHub Releases if a version was passed
if [ -n "$VERSION" ]; then
    if ! command -v gh >/dev/null 2>&1; then
        echo "!! gh CLI not installed — install with: brew install gh"
        exit 1
    fi
    echo ""
    echo "==> Publishing release $VERSION to GitHub..."
    ASSETS=("$ZIP_PATH")
    [ -f "$DMG_PATH" ] && ASSETS+=("$DMG_PATH")

    gh release create "$VERSION" \
        --title "$APP_NAME $VERSION" \
        --notes "Automated build of $APP_NAME $VERSION." \
        "${ASSETS[@]}"

    echo ""
    echo "==> Release published. Download URL:"
    echo "    https://github.com/NardoneyLand/updated-OSRS-tracker/releases/tag/$VERSION"
else
    echo ""
    echo "==> Skipping GitHub publish (pass a tag, e.g. ./release.sh v1.0.0)."
fi
