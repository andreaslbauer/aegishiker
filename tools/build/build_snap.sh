#!/bin/bash
# ==============================================================================
# aegishiker - Snap Package Builder
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Snap Package Builder: aegishiker${NC}"
echo -e "${BLUE}========================================${NC}"

# 1. Clean stale LXD containers from interrupted builds
if command -v lxc &> /dev/null; then
    STALE=$(lxc list --project snapcraft -c n --format csv 2>/dev/null | grep "snapcraft-aegishiker-" || true)
    if [ -n "$STALE" ]; then
        echo -e "${YELLOW}[i] Cleaning stale LXD build containers...${NC}"
        for c in $STALE; do
            lxc stop --project snapcraft local:$c --force 2>/dev/null || true
            lxc delete --project snapcraft local:$c --force 2>/dev/null || true
        done
    fi
fi

# 2. Docker / LXD Network bridge fix if Docker is active
if systemctl is-active docker &>/dev/null; then
    echo -e "${YELLOW}[i] Applying Docker-LXD firewall forwarding rule...${NC}"
    sudo iptables -P FORWARD ACCEPT 2>/dev/null || true
    sudo sysctl -w net.ipv4.ip_forward=1 > /dev/null 2>&1 || true
    sudo iptables -C DOCKER-USER -j ACCEPT 2>/dev/null || \
        sudo iptables -I DOCKER-USER 1 -j ACCEPT 2>/dev/null || true
    lxc network set lxdbr0 dns.nameservers 8.8.8.8,8.8.4.4 2>/dev/null || true
fi

# 3. Build snap with LXD
echo -e "${BLUE}[*] Running snapcraft pack...${NC}"
snapcraft pack --use-lxd --verbose

echo -e "${GREEN}[✓] Snap built successfully!${NC}"
ls -lh *.snap | head -n 1
