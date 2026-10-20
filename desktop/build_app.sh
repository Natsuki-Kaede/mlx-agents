#!/bin/zsh
# Build "MLX Agents.app": the UI (ui/ -> desktop/dist), the icon, and a thin
# bundle whose executable starts this project's venv. The bundle points at this folder, so keep it in place.
set -euo pipefail
HERE="${0:A:h}"
ROOT="${HERE:h}"
APP="$HERE/build/MLX Agents.app"

(cd "$ROOT/ui" && npx vite build --logLevel warn)
mkdir -p "$HERE/build"
"$ROOT/.venv/bin/python" "$HERE/make_icon.py"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$HERE/build/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>MLX Agents</string>
  <key>CFBundleDisplayName</key><string>MLX Agents</string>
  <key>CFBundleIdentifier</key><string>local.mlx-agents</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>MLXAgents</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
cat > "$APP/Contents/MacOS/MLXAgents" <<LAUNCHER
#!/bin/zsh
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m desktop.app
LAUNCHER
chmod +x "$APP/Contents/MacOS/MLXAgents"
echo "built: $APP"
