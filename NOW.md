# NOW

This is the four-item this-week view of TODO.md, two lines each, pointing back into the full list. The shape of this file is checked by bin/check-now-focused.py.

## This week

- [ ] **F432** Make the gateway alert loop register IBKR when the Gateway is logged in with no API connection. On strategylab01 a restart during login leaves bots unable to trade. See Hardening in TODO.md.
- [ ] **F431** Save the Gateway alert cooldown to a small JSON file, so a restart stops sending one extra alert for a known problem. Same file as F432. See Hardening in TODO.md.
- [ ] **F424** Run bin/worker-pytest.sh against a real worker for the first time and write down the result and how long it took. The wrapper went in untested, this is the proof. See Infra in TODO.md.
- [ ] **F426** Stop months old Fetch failed lines sitting above today's activity on a bot card. A date divider, an age filter or a clear log button. See Polish in TODO.md.

## Waiting on John

Full text, evidence and a recommendation for each one live in ~/.local/state/strategylab/john-questions.md.

- Start the phase 1 discovery build now that all four data panels are in? (F404)
- Which time window should the one-shot fresh-data test of the insider-sell premise use? (F393)
- Approve a charter for "for whom do filings lead price?" (F347)
- Re-open paid delisted-price data, or stay on free sources? (F318)
- Cast a wider net for the insider-buy study, or shelve it? (question 5, was F343, retired)
- Shelve the R-2 distress-recovery study, or widen the event net before running it? (question 6, was F372, retired)
- Do we split the two biggest backend files, or leave them long? (F63)
- Does the node based strategy builder grow richer nodes, or stay at small nodes? (question 8, was F272, retired)
- Should each dose level get its own random seed, or keep the shared one? (F382)
- Do we still want to measure parallel against sequential review dispatch? (F280)
