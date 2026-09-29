# NOW

This is the four-item this-week view of TODO.md, two lines each, pointing back into the full list. The shape of this file is checked by bin/check-now-focused.py.

## This week

- [ ] **F440** After the next weekday, check the VM's IBC log for Restart in progress lines and one clean login per day. The Gateway writes AutoRestart=1 back after the unit clears it. See Hardening in TODO.md.
- [ ] **F431** Save the Gateway alert cooldown to a small JSON file, so a restart stops sending one extra alert for a known problem. The state is AlertState in backend/gateway.py. See Hardening in TODO.md.
- [ ] **F424** Run bin/worker-pytest.sh against a real worker for the first time and write down the result and how long it took. The wrapper went in untested, this is the proof. See Infra in TODO.md.
- [ ] **F404** Start the discovery scan John said go to: spec and plan first, then the join layer over the four data panels and the return table. See Architecture in TODO.md.

## Waiting on John

None open. The two questions from 2026-09-29 (port-80 allowlist, port 22 from the Mac) are answered, and the decisions are in JOURNAL.md.
