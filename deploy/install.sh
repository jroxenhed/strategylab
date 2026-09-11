#!/usr/bin/env bash
# StrategyLab installer for Rocky Linux 9. Idempotent; run as root.
#
#   SL_PUBLIC_URL=http://strategylab01 bash deploy/install.sh
#
# Env knobs (all optional):
#   SL_PUBLIC_URL   URL the browser uses to reach nginx (baked into the frontend build). Default http://<hostname -s>
#   SL_REPO_DIR     checkout to deploy from. Default /opt/strategylab
#   SL_USER         service user. Default strategylab
#   SL_SKIP_GATEWAY set to 1 to skip the IB Gateway / IBC download+install
#   GATEWAY_VRSN    Gateway major version dir, no dot. Default 1045
#   IBC_VRSN        IBC release. Default 3.23.0
#   SL_HTTP_ALLOW   space-separated IPv4 addresses/CIDRs allowed to reach port 80
#                   (e.g. the edge proxy + the WireGuard NAT address, "172.16.17.115 192.168.216.0/24 172.16.16.175").
#                   Required whenever firewalld is active — port 80 fronts /vnc/,
#                   /websockify, and /api/gateway/command/{cmd}, none of which have
#                   their own auth, so it must never be opened to the whole network.
#   SL_HTTP_ALLOW_ANY  set to 1 to explicitly open port 80 network-wide instead
#                      (bypasses the SL_HTTP_ALLOW requirement) — only for a VM that
#                      is otherwise fully firewalled off, not the normal path.
set -euo pipefail

SL_REPO_DIR="${SL_REPO_DIR:-/opt/strategylab}"
SL_USER="${SL_USER:-strategylab}"
SL_PUBLIC_URL="${SL_PUBLIC_URL:-http://$(hostname -s)}"
GATEWAY_VRSN="${GATEWAY_VRSN:-1045}"
IBC_VRSN="${IBC_VRSN:-3.23.0}"
SL_HTTP_ALLOW="${SL_HTTP_ALLOW:-}"
SL_HTTP_ALLOW_ANY="${SL_HTTP_ALLOW_ANY:-0}"
SL_HOME="/home/${SL_USER}"
DATA_DIR="/var/lib/strategylab"
WEB_ROOT="/var/www/strategylab"
IBC_DIR="/opt/ibc"
DEPLOY="${SL_REPO_DIR}/deploy"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"
[[ -d "$SL_REPO_DIR/backend" ]] || die "no checkout at $SL_REPO_DIR (git clone the repo there first)"
[[ -f /etc/os-release ]] && grep -qiE 'rocky|rhel|almalinux' /etc/os-release || die "this installer targets Rocky/RHEL 9"

# --- OS packages -------------------------------------------------------------
log "OS packages"
dnf -y install epel-release >/dev/null
dnf -y module reset nodejs >/dev/null 2>&1 || true
dnf -y module enable nodejs:20 >/dev/null
dnf -y install \
  git curl unzip rsync policycoreutils-python-utils \
  python3.12 python3.12-pip \
  nodejs npm \
  nginx \
  xorg-x11-server-Xvfb tigervnc-server-minimal \
  libX11 libXext libXrender libXtst libXi libXrandr libXcursor libxcb fontconfig dejavu-sans-fonts \
  >/dev/null
# tigervnc-server-minimal is expected to ship x0vncserver (assumed for Rocky 9; verified by the check below)
command -v x0vncserver >/dev/null || dnf -y install tigervnc-server >/dev/null
command -v x0vncserver >/dev/null || die "x0vncserver not found after install — check tigervnc packages"

# F429: noVNC + websockify — bridges the loopback VNC mirror to the browser
# (/vnc/ via nginx). Package names ASSUMED from EPEL (not yet installed on a
# live VM at spec time) — die with a clear message if they don't resolve
# rather than silently skipping the Gateway panel's "Open screen" link.
dnf -y install novnc python3-websockify >/dev/null || die "novnc / python3-websockify not found in EPEL — check package names for this Rocky/RHEL release"
[[ -d /usr/share/novnc ]] || die "novnc installed but /usr/share/novnc missing — check the novnc package's file layout"
command -v websockify >/dev/null || die "websockify not on PATH after installing python3-websockify"

# --- user + dirs -------------------------------------------------------------
log "service user + directories"
id "$SL_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$SL_HOME" --shell /sbin/nologin "$SL_USER"
install -d -m 750 -o root -g "$SL_USER" /etc/strategylab
install -d -m 750 -o "$SL_USER" -g "$SL_USER" "$DATA_DIR" "$SL_HOME/Jts" /var/log/ibc "$SL_REPO_DIR/backend/data"
# backend/data must exist: the unit lists it in ReadWritePaths and systemd fails NAMESPACE (226) if it is missing
chown -R "$SL_USER:$SL_USER" "$SL_REPO_DIR"

for f in backend.env ibc.env; do
  if [[ ! -f /etc/strategylab/$f ]]; then
    echo "!! /etc/strategylab/$f missing — copy it from the secrets store (see deploy/env.example). Services will not start without it."
  else
    chown root:"$SL_USER" /etc/strategylab/$f; chmod 640 /etc/strategylab/$f
  fi
done

# --- backend venv ------------------------------------------------------------
log "backend venv (python3.12)"
sudo -u "$SL_USER" bash -c "
  set -e; cd '$SL_REPO_DIR/backend'
  if [[ ! -x venv/bin/python ]] || ! venv/bin/python -c 'import sys; assert sys.version_info[:2]==(3,12)' 2>/dev/null; then
    rm -rf venv; python3.12 -m venv venv
  fi
  venv/bin/pip install -q --upgrade pip
  venv/bin/pip install -q -r requirements.txt
"

# --- frontend build ----------------------------------------------------------
log "frontend build (VITE_API_URL=$SL_PUBLIC_URL)"
sudo -u "$SL_USER" bash -c "
  set -e; cd '$SL_REPO_DIR/frontend'
  npm ci --no-audit --no-fund --loglevel=error
  VITE_API_URL='$SL_PUBLIC_URL' npm run build --silent
"
install -d -m 755 "$WEB_ROOT"
rsync -a --delete "$SL_REPO_DIR/frontend/dist/" "$WEB_ROOT/"
restorecon -R "$WEB_ROOT" 2>/dev/null || true

# --- nginx -------------------------------------------------------------------
log "nginx"
install -m 644 "$DEPLOY/nginx/strategylab.conf" /etc/nginx/conf.d/strategylab.conf
# default server block in nginx.conf would shadow ours on :80 — disable it if present
if grep -q 'server {' /etc/nginx/nginx.conf && ! grep -q 'STRATEGYLAB-DEFAULT-DISABLED' /etc/nginx/nginx.conf; then
  sed -i '/^    server {/,/^    }/{s/^/#/}' /etc/nginx/nginx.conf
  echo '# STRATEGYLAB-DEFAULT-DISABLED: default server block commented out by deploy/install.sh' >> /etc/nginx/nginx.conf
fi
setsebool -P httpd_can_network_connect 1 2>/dev/null || true
nginx -t
systemctl enable --now nginx
systemctl reload nginx
if command -v firewall-cmd >/dev/null && systemctl is-active -q firewalld; then
  if [[ -z "$SL_HTTP_ALLOW" && "$SL_HTTP_ALLOW_ANY" != "1" ]]; then
    die "SL_HTTP_ALLOW is empty. Port 80 fronts /vnc/, /websockify, and /api/gateway/command/{cmd} — none of which have their own auth — so it must be limited to the edge proxy and trusted tunnel sources (e.g. SL_HTTP_ALLOW=\"172.16.17.115 192.168.216.0/24 172.16.16.175\"). Set SL_HTTP_ALLOW_ANY=1 to open it network-wide instead (not the normal path)."
  fi
  # Remove the generic http service (opens 80 to the whole zone) — tolerant of it not being present.
  firewall-cmd -q --permanent --remove-service=http 2>/dev/null || true
  if [[ "$SL_HTTP_ALLOW_ANY" == "1" ]]; then
    firewall-cmd -q --permanent --add-service=http
  else
    for src in $SL_HTTP_ALLOW; do
      rule="rule family=\"ipv4\" source address=\"$src\" port port=\"80\" protocol=\"tcp\" accept"
      firewall-cmd -q --permanent --query-rich-rule="$rule" >/dev/null 2>&1 || \
        firewall-cmd -q --permanent --add-rich-rule="$rule"
    done
  fi
  firewall-cmd -q --reload
fi

# --- IB Gateway + IBC --------------------------------------------------------
if [[ "${SL_SKIP_GATEWAY:-0}" != "1" ]]; then
  GW_DIR="$SL_HOME/Jts/ibgateway/$GATEWAY_VRSN"
  if [[ ! -d "$GW_DIR/jars" ]]; then
    log "IB Gateway (stable standalone, bundled JRE) -> $GW_DIR"
    tmp=$(mktemp -d); chmod 755 "$tmp"   # mktemp is 0700 root; the installer runs as $SL_USER
    curl -fsSL -o "$tmp/ibgateway.sh" \
      https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
    chmod +x "$tmp/ibgateway.sh"
    # install4j unattended install; the installer writes into -dir
    sudo -u "$SL_USER" "$tmp/ibgateway.sh" -q -dir "$GW_DIR"
    rm -rf "$tmp"
    [[ -d "$GW_DIR/jars" ]] || die "Gateway install did not produce $GW_DIR/jars — check the installer's version dir (GATEWAY_VRSN=$GATEWAY_VRSN)"
  else
    log "IB Gateway already present at $GW_DIR"
  fi

  if [[ ! -f "$IBC_DIR/version" ]] || [[ "$(cat "$IBC_DIR/version")" != "$IBC_VRSN" ]]; then
    log "IBC $IBC_VRSN -> $IBC_DIR"
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/ibc.zip" "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VRSN}/IBCLinux-${IBC_VRSN}.zip"
    rm -rf "$IBC_DIR"; install -d "$IBC_DIR"
    unzip -q "$tmp/ibc.zip" -d "$IBC_DIR"; rm -rf "$tmp"
    chmod +x "$IBC_DIR"/*.sh "$IBC_DIR"/scripts/*.sh
  fi

  log "render IBC config.ini from /etc/strategylab/ibc.env"
  if [[ -f /etc/strategylab/ibc.env ]]; then
    set -a; # shellcheck disable=SC1091
    . /etc/strategylab/ibc.env; set +a
    : "${IB_USER:?IB_USER missing in ibc.env}" "${IB_PASSWORD:?IB_PASSWORD missing in ibc.env}"
    IB_TRADING_MODE="${IB_TRADING_MODE:-paper}"
    sed -e "s|@IB_USER@|${IB_USER}|" -e "s|@IB_PASSWORD@|${IB_PASSWORD}|" -e "s|@IB_TRADING_MODE@|${IB_TRADING_MODE}|" \
      "$DEPLOY/ibc/config.ini.template" > "$IBC_DIR/config.ini"
    chown "$SL_USER:$SL_USER" "$IBC_DIR/config.ini"; chmod 600 "$IBC_DIR/config.ini"
    unset IB_USER IB_PASSWORD
  fi
  chown -R "$SL_USER:$SL_USER" "$IBC_DIR"
fi

# --- systemd -----------------------------------------------------------------
log "systemd units"
install -m 644 "$DEPLOY"/systemd/*.service "$DEPLOY"/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now strategylab-backend
if [[ "${SL_SKIP_GATEWAY:-0}" != "1" ]]; then
  systemctl enable --now strategylab-xvfb strategylab-vnc strategylab-novnc
  systemctl enable strategylab-ibc strategylab-ibc-restart.timer
  systemctl start strategylab-ibc-restart.timer
  if systemctl is-active -q strategylab-ibc; then
    systemctl restart strategylab-ibc
  else
    echo
    echo "!! strategylab-ibc is enabled but NOT started: the first Gateway login needs a human on VNC for the 2FA push."
    echo "   ssh -L 5901:127.0.0.1:5901 $(hostname -s)   then VNC to localhost:5901, then: systemctl start strategylab-ibc"
  fi
fi

# --- smoke -------------------------------------------------------------------
log "smoke"
for _ in $(seq 1 30); do curl -sf -m 2 http://127.0.0.1:8000/api/cache >/dev/null && break; sleep 1; done
curl -sf -m 5 http://127.0.0.1:8000/api/cache >/dev/null && echo "backend: OK (127.0.0.1:8000)" || echo "backend: NOT answering — journalctl -u strategylab-backend"
curl -sf -m 5 -o /dev/null http://127.0.0.1/ && echo "nginx:   OK (:80)" || echo "nginx:   NOT serving — nginx -t / journalctl -u nginx"
echo "done. Frontend: $SL_PUBLIC_URL"
