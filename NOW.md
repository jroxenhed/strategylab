# NOW

This is the four-item this-week view of TODO.md, two lines each, pointing back into the full list. The shape of this file is checked by bin/check-now-focused.py.

## This week

- [ ] **F455** Make the Alpaca broker stop last past the close: it is a day order today, so from the second day only the bot's bar-close check guards a position. Send it again each morning or use GTC. See Hardening in TODO.md.
- [ ] **F431** Save the Gateway alert cooldown to a small JSON file, so a restart stops sending one extra alert for a known problem. The state is AlertState in backend/gateway.py. See Hardening in TODO.md.
- [ ] **F424** Run bin/worker-pytest.sh against a real worker for the first time and write down the result and how long it took. The wrapper went in untested, this is the proof. See Infra in TODO.md.
- [ ] **F404** Start the discovery scan John said go to: spec and plan first, then the join layer over the four data panels and the return table. See Architecture in TODO.md.

## Waiting on John

Nothing. Node builder Waves 0 to 7 run on strategylab01 since 2026-10-16, and the live paper gate G1 passed from 16 to 20 Oct. Wave 8 (bar mode) and the Fable UI/UX pass start when John says go.
