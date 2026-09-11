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
SL_PUBLIC_URL=http://strategylab01 bash /opt/strategylab/deploy/install.sh
```

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

## Diagnostics

- Backend: `journalctl -u strategylab-backend -f`, `curl -s localhost:8000/api/cache`
- IBKR errors: `curl -s localhost:8000/api/debug/ibkr-errors` (50-entry ring buffer)
- IBC: `/var/log/ibc/ibc-*_GATEWAY-1045_<Weekday>.txt` — look for
  `Login has completed`; `Re-login is required` without it means the session is
  half-dead and needs a human on VNC.
- Health from the Mac: `curl -s http://strategylab01/api/cache`
