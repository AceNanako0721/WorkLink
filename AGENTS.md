# WorkLink repository instructions

This is a public repository. Keep user identities, real tenant URLs, folder IDs,
credentials, browser sessions, messages, attachments, databases, logs and screenshots
outside the checkout. Use the external private configuration rather than hardcoded
personal values. Public examples use placeholders and synthetic fixtures.

Original local notes and evidence are preserved in the external private directory.
Do not copy them into documentation, commits, issues, PRs, CI logs or release assets.
Never print sensitive values when reporting a failed privacy check.

Before committing or pushing, run `python scripts/check-public.py --history` and
`python -m unittest discover -s tests -v`. Install local guards with
`python scripts/install-hooks.py` in a new clone. Do not bypass a failed guard;
repair the actual source of the leak. Check the full history before first publication.
Also run `node --test tests/edge-extension.test.cjs` before committing or pushing.

Use `scripts/release.ps1 <version>` for authorized releases. Build packages from a
clean tagged checkout and the explicit tracked-file allowlist, never from a whole
development directory. Real Teams/SharePoint smoke tests stay local; CI uses fixtures.

The first bridge uses a loopback Python service and a narrowly scoped Edge extension.
Preserve `/chat`, `/choose` and `omp：` interaction requirements, and distinguish
protocol/fixture checks, live browser effects, and a full installed OMP session.
No software license has been selected; do not choose one without the user's decision.
