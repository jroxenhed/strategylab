# Deploying StrategyLab on a Rocky 9 VM

Target: one always-on VM (office Proxmox, `strategylab01`) running the backend,
the static frontend behind nginx, and IB Gateway under IBC on a virtual display.
Provisioning of the VM itself (Proxmox clone, IP, DNS) is owned by fleet ops;
everything from "fresh Rocky 9 with root" onward is owned by this directory.

## Layout on the VM

| Path | What | Owner |
|---|---|---|
| `/opt/strategylab` | git checkout of this repo | `strategylab` |
| `/opt/strategylab/backend/venv` | Python **3.12** venv (pandas-ta pins `>=3.12`; numpy/scipy pins have no 3.14 wheels) | `strategylab` |
| `/var/www/strategylab` | built frontend (`frontend/dist`), served by nginx on :80 | root |
| `/var/lib/strategylab` | `bots.json`, `trade_journal.json`, cache — via `STRATEGYLAB_DATA_DIR` | `strategylab` |
| `/opt/ibc` | IBC 3.23.0 (Linux zip) + rendered `config.ini` | `strategylab` |
| `/home/strategylab/Jts/ibgateway/1045` | IB Gateway 10.45 (bundles its own JRE — no system Java) | `strategylab` |
| `/etc/strategylab/backend.env` | backend secrets (Alpaca, Polygon, Slack, `IBKR_HOST/PORT`) — mode 0640 root:strategylab | root |
| `/etc/strategylab/ibc.env` | `IB_USER`, `IB_PASSWORD`, `IB_TRADING_MODE` — mode 0640 root:strategylab | root |

Secrets never live in the repo. Fleet rule: they land in the bastion01 secrets
store first and are copied to `/etc/strategylab/` from there.

## Services

| Unit | Does | Listens |
|---|---|---|
| `strategylab-backend.service` | uvicorn `main:app`, bot loops included | 127.0.0.1:8000 |
| `nginx.service` | static frontend + `/api/` reverse proxy to the backend | 0.0.0.0:80 |
| `strategylab-xvfb.service` | `Xvfb :1` virtual display for the Gateway | — |
| `strategylab-vnc.service` | `x0vncserver` mirror of `:1`, **localhost only** | 127.0.0.1:5901 |
| `strategylab-ibc.service` | IBC → IB Gateway on `DISPLAY=:1` | 127.0.0.1:4002 (API) |
| `strategylab-ibc-restart.timer` | restarts the Gateway daily at 05:00 America/New_York (IBC's own `AutoRestartTime` is unreliable on Gateway ≥ 10.34) | — |

The frontend is built with `VITE_API_URL=<public url>` so browser calls go to
`http://strategylab01/api/...` and nginx proxies them same-origin. The backend
itself is never exposed beyond localhost.

## Install

```sh
# as root on a fresh Rocky 9 minimal, over WireGuard
dnf -y install git
git clone https://github.com/jroxenhed/strategylab /opt/strategylab
# put the two secret files in place first (see env.example)
install -d -m 750 -o root -g strategylab /etc/strategylab   # after the user exists; install.sh creates it too
SL_PUBLIC_URL=http://strategylab01 SL_HTTP_ALLOW="172.16.17.115 192.168.216.0/24 172.16.16.175" \
  bash /opt/strategylab/deploy/install.sh
```

`SL_HTTP_ALLOW` is a space-separated list of IPv4 addresses/CIDRs allowed to
reach port 80 (the edge proxy plus the WireGuard NAT address — see the trust
model note below). If firewalld is active, install.sh refuses to run with it
empty, unless `SL_HTTP_ALLOW_ANY=1` is set to explicitly open the port
network-wide instead.

### Trust model

Google sign-in lives on the edge proxy, not on this VM — nginx and the
backend do not authenticate anyone themselves. The VM's port 80 is scoped by
firewalld rich rules (`SL_HTTP_ALLOW`) to accept only the edge proxy and the
WireGuard NAT address. Anyone who can reach port 80 directly is, by
construction, already inside the trusted network (past the edge or on the
tunnel) — they can use the Gateway screen ("Open screen" / VNC) and the
Gateway commands (`RESTART` / `RECONNECTACCOUNT` / `RECONNECTDATA` / `STOP`)
without a second sign-in. Keeping `SL_HTTP_ALLOW` scoped tightly is what
actually enforces this — it is not a documentation-only assumption.

Run it from a login shell (ssh), not as a transient systemd unit: under SELinux a transient unit puts rsync in a domain that cannot write `/var/www` (hit once, 2026-09-11).

`install.sh` is idempotent: rerun it after `git pull` to rebuild the venv/frontend
and reload units. It stops short of starting `strategylab-ibc.service` on first
run — the first Gateway login needs a human watching VNC for the 2FA prompt.

## First login and the 2FA ritual

```sh
ssh -L 5901:127.0.0.1:5901 strategylab01        # from the Mac, over WireGuard
# then a VNC viewer at localhost:5901
systemctl start strategylab-ibc
```

Approve the push on IBKR Mobile when it arrives. IBKR forces a fresh 2FA after the
Sunday ~01:00 ET weekly reset, so expect one push per week, otherwise the daily
05:00 restart re-logs silently (observed on the Mac for months; carried as
*told* until seen on the VM).

**One IBKR session per account.** The moment the Gateway runs here, the one on
the Mac must stay stopped (`launchctl unload ~/Library/LaunchAgents/local.ibc-gateway.plist`).
Two logins fight, market data dies with Error 162, and a re-login loop can
lock the account ("Too many failed login attempts", seen 2026-09-09).

## Migrating state from the Mac

The VM takes ssh from bastion01 only, so state moves through the secrets store.
**Order matters: stop → copy → start.** A running backend saves its in-memory
state on SIGTERM (backup depth 1), so copying first and restarting after
overwrites the copied file and then its `.bak` (hit once, 2026-09-11).

```sh
# Mac: backend stopped, Gateway unloaded, then
scp backend/data/bots.json backend/data/trade_journal.json bastion01:/root/.mfit/secrets/strategylab/state/
# VM (as root):
systemctl stop strategylab-backend
scp bastion01:/root/.mfit/secrets/strategylab/state/*.json /var/lib/strategylab/
chown strategylab:strategylab /var/lib/strategylab/*.json && chmod 640 /var/lib/strategylab/*.json
systemctl start strategylab-backend
curl -s 127.0.0.1:8000/api/bots | grep -o '"bot_id"' | wc -l   # expect the bot count
```

Bots are restored in `stopped` state; start them from the UI.

WireGuard note: the VM sees tunnel clients as the MikroTik's NAT address
(172.16.16.175), not 192.168.216.x — firewall rules for "reach from WireGuard"
must allow that address.

## Sizing

Measured on the Mac (probed 2026-09-11): backend RSS ~0.5–1.2 GB with 11 bots
polling; Gateway JVM ~0.8–1.5 GB (no `-Xmx` set, so it takes up to ¼ of RAM by
default). 4 GiB works, 6 GiB leaves headroom for a walk-forward run without
swapping; 2 vCPU is fine for live trading, 4 helps optimizer grids. Research
compute stays on mfcore01 either way.

## Gateway panel

F428/F429: the app's Trading tab shows a live IB Gateway state bar (backend:
`backend/gateway.py` + `GET/POST /api/gateway/*`, frontend: `GatewayPanel.tsx`).
It parses the newest IBC log for the last-seen marker line, so it needs
`IBC_LOG_DIR` pointed at IBC's log directory — `strategylab-backend.service`
sets `Environment=IBC_LOG_DIR=/var/log/ibc` to match where IBC actually
writes on this VM.

State meanings (from the log marker, most recent line wins):
- `logged_in` — normal, no action needed.
- `awaiting_2fa` — approve the push on IBKR Mobile (the panel cannot do this
  for you — see "Out of scope" in the F428 plan).
- `relogin_required` / `bad_credentials` — session is half-dead; open the
  Gateway screen and check the login dialog.
- `locked_out` — too many failed attempts; wait out IBKR's lockout window
  before retrying.
- `restarting` / `logging_in` — transient, no action needed unless it sticks.
- `down` — no recent log activity and the API isn't connected either
  (only fires once the panel has seen a prior `logged_in`, so a cold boot
  doesn't alert).
- `unknown` — log file present but no marker matched yet (e.g. fresh restart).

Alerts (ntfy.sh `notify()` + Slack via `SLACK_WEBHOOK_URL`) fire on
transition into a needs-a-human state and re-fire every 30 min while stuck
there; disable with `GATEWAY_ALERTS=0` in `backend.env`.

Commands (`RESTART` / `RECONNECTACCOUNT` / `RECONNECTDATA` / `STOP`) go to
IBC's CommandServer over a loopback socket — `CommandServerPort=7462` /
`ControlFrom=127.0.0.1` / `BindAddress=127.0.0.1` in
`deploy/ibc/config.ini.template`. If the panel shows the command buttons
disabled, the port isn't reachable — check `config.ini` was rendered with
those keys and IBC is actually running.

"Open screen" links to `/vnc/vnc.html?autoconnect=1&resize=scale&path=websockify`
— nginx proxies `/vnc/` (static noVNC assets) and `/websockify` (the
websocket) to `strategylab-novnc` (`websockify --web=/usr/share/novnc
127.0.0.1:6080 127.0.0.1:5901`), which bridges to the same loopback VNC
mirror (`strategylab-vnc`, port 5901) used for the manual 2FA ritual above.
Neither noVNC nor the VNC mirror has its own auth — both are reachable only
through whatever edge auth fronts this nginx server block, same as every
other `/` route.

## Diagnostics

- Backend: `journalctl -u strategylab-backend -f`, `curl -s localhost:8000/api/cache`
- Gateway panel: `curl -s localhost:8000/api/gateway/status`; noVNC bridge:
  `journalctl -u strategylab-novnc -f`
- IBKR errors: `curl -s localhost:8000/api/debug/ibkr-errors` (50-entry ring buffer)
- IBC: `/var/log/ibc/ibc-*_GATEWAY-1045_<Weekday>.txt` — look for
  `Login has completed`; `Re-login is required` without it means the session is
  half-dead and needs a human on VNC.
- Health from the Mac: `curl -s http://strategylab01/api/cache`

## How to hand an app to the cockpit

Learned on 2026-09-11, when mfIT1 built strategylab01 from this directory in one evening and hit three bugs that this list prevents.

1. Put everything after "fresh OS with root" in one idempotent `install.sh`. Run it from a login shell, never as a transient systemd unit (SELinux gives rsync a domain that cannot write `/var/www`).
2. Any file the installer downloads and then runs as the service user must be readable by that user (`chmod 755` the mktemp dir).
3. Every path in a unit's `ReadWritePaths` must exist. Create it in the installer.
4. Secrets go to `bastion01:/root/.mfit/secrets/<app>/` first. The cockpit copies them to `/etc/<app>/`. Never paste a secret in the peer channel.
5. App state (bots.json, journal) goes through the same store. The cockpit owns the copy: stop the service, copy, start.
6. Name the OS packages and mark the ones you have not seen installed as assumed. Fail loudly on a missing package.
7. Say which ports must be open and from where. Port 80 here is limited to the edge proxy and the tunnel (`SL_HTTP_ALLOW`). Sign-in lives on the edge.
8. Say what needs a human (the first Gateway 2FA over VNC) and leave that service enabled but not started.
