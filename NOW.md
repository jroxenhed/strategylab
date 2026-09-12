# NOW

This is the four-item this-week view of TODO.md, two lines each, pointing back into the full list. The shape of this file is checked by bin/check-now-focused.py.

## This week

- [ ] **F432** Make the gateway alert loop register IBKR when the Gateway is logged in with no API connection. On strategylab01 a restart during login leaves bots unable to trade. See Hardening in TODO.md.
- [ ] **F431** Save the Gateway alert cooldown to a small JSON file, so a restart stops sending one extra alert for a known problem. Same file as F432. See Hardening in TODO.md.
- [ ] **F424** Run bin/worker-pytest.sh against a real worker for the first time and write down the result and how long it took. The wrapper went in untested, this is the proof. See Infra in TODO.md.
- [ ] **F404** Start the discovery scan John said go to: spec and plan first, then the join layer over the four data panels and the return table. See Architecture in TODO.md.

## Waiting on John

None open. The ten questions from the 2026-09-12 rewrite were answered the same day; the decisions are in JOURNAL.md and the items moved in TODO.md.
