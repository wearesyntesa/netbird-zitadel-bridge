#!/bin/bash
set -euo pipefail

INSTALL_USER="netbird-bridge"
CONFIG_DIR="/etc/netbird-zitadel-bridge"
STATE_DIR="/var/lib/netbird-zitadel-bridge"
BINARY="/usr/local/bin/netbird-zitadel-bridge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/6] Checking dependencies..."
for cmd in python3 pip3; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: $cmd is required but not installed."
    exit 1
  fi
done

echo "[2/6] Installing Python dependencies..."
pip3 install --quiet requests pyyaml

echo "[3/6] Creating system user..."
if ! id "$INSTALL_USER" &>/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$INSTALL_USER"
  echo "  Created user: $INSTALL_USER"
else
  echo "  User $INSTALL_USER already exists."
fi

echo "[4/6] Creating directories..."
mkdir -p "$CONFIG_DIR" "$STATE_DIR"
chown "$INSTALL_USER:$INSTALL_USER" "$STATE_DIR"
chmod 750 "$CONFIG_DIR" "$STATE_DIR"

echo "[5/6] Installing files..."
cat > "$BINARY" << EOF
#!/bin/bash
exec python3 $SCRIPT_DIR/netbird_zitadel_bridge.py "\$@"
EOF
chmod +x "$BINARY"

# Install systemd units
cp "$SCRIPT_DIR/netbird-zitadel-bridge.service" /etc/systemd/system/
cp "$SCRIPT_DIR/netbird-zitadel-bridge.timer" /etc/systemd/system/

echo "[6/6] Enabling systemd timer..."
systemctl daemon-reload
systemctl enable --now netbird-zitadel-bridge.timer

echo ""
echo "Installation complete."
echo ""
echo "Next steps:"
echo "  1. Copy config: cp $SCRIPT_DIR/config.yml.example $CONFIG_DIR/config.yml"
echo "  2. Edit config: nano $CONFIG_DIR/config.yml"
echo "  3. Fix ownership: chown root:$INSTALL_USER $CONFIG_DIR/config.yml && chmod 640 $CONFIG_DIR/config.yml"
echo "  4. Test run: systemctl start netbird-zitadel-bridge.service"
echo "  5. Check logs: journalctl -u netbird-zitadel-bridge -f"
