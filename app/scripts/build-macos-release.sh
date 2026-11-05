#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export APPLE_SIGNING_IDENTITY="Developer ID Application: Cheng Tan (9YJL5M4BY8)"
npm run tauri -- build --config src-tauri/tauri.release.conf.json "$@"
app_path="src-tauri/target/release/bundle/macos/Tingo.app"
requirement='anchor apple generic and certificate leaf[subject.OU] = "9YJL5M4BY8"'
codesign --verify --deep --strict -R "=$requirement" "$app_path"
codesign --verify --strict -R "=$requirement" "$app_path/Contents/Resources/tingo-recorder"
