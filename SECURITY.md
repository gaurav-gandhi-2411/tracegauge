# Security policy

## Reporting a vulnerability

Please report a suspected vulnerability privately by email to gauravgandhi429@gmail.com, not in a public issue. Include the tracegauge version (`tracegauge --version`), what you did, and what you observed.

This is a solo-maintained project: expect an acknowledgement within a few days, not hours.

## Scope worth knowing

tracegauge reads Claude Code session transcripts, which can contain source code and secrets. Scoring and the dashboard (`tracegauge serve`, bound to `127.0.0.1`) make no external network calls. The only working egress is the opt-in API judge, which sends session snippets to your own model provider on per-session consent (see PRIVACY.md). Reports about either path are in scope.

## Supported versions

Only the latest release on PyPI receives fixes.
