# Roadmap

What tracegauge does today, what is planned next and why in that order, and what it will not do.
This is a statement of intent by a solo maintainer, not a schedule: there are no dates here and
nothing below is a promise. [CHANGELOG.md](CHANGELOG.md) is the record of what shipped.

## Shipped

Every line below is a claim from [CHANGELOG.md](CHANGELOG.md); the version in brackets is the entry
that introduced it.

- **A Claude Code adapter** that turns a session transcript into a digest. It counts each API response's
  usage once, which corrected totals that had been counted once per content block [0.15.0], and it rolls
  subagent transcripts into their parent session [0.15.0].
- **Three separate readings, never blended into one score:** token economy against a band for the same
  task type, deterministic waste detection, and an optional local LLM judge for trajectory quality
  [0.5.0; see [docs/METHODOLOGY.md](docs/METHODOLOGY.md)].
- **Cost from a bundled, dated price table.** A model the table cannot price is reported as unpriced
  instead of being priced at a guessed rate [0.10.2]. A period report (`tracegauge cost`, then named
  `tes cost`) [0.11.0] and a plan comparison (`--roi`) [0.12.0].
- **A live monitor and a cost alarm** [0.10.0].
- **A local dashboard** with a sortable session list [0.8.0].
- **A cache-churn detector** that finds turns where cached context was written again instead of read, and
  prices the difference [0.16.0; see [docs/CACHE_CHURN.md](docs/CACHE_CHURN.md)].
- **The `tracegauge` name** for the package, command and environment variables, with the old `tes` names
  kept as deprecated aliases until 0.18, and **Apache-2.0** licensing from 0.16.0 [0.16.0].
- **A DCO sign-off check and a secret scan in CI** [0.16.0].

Claude Code is the only supported agent today.

## Next: more agents, one at a time

Each agent needs its own adapter and a fresh validation run, not a flag. The order below follows how
much structured usage data each agent writes to disk, which decides how much of an adapter can be
checked against the source rather than guessed. The format notes are in
[docs/ADAPTERS.md](docs/ADAPTERS.md), with each claim marked VERIFIED or UNVERIFIED.

1. **Codex CLI.** Sessions are JSON Lines, one file each, and the source documents `token_count` events
   that carry cumulative and per-call token usage, so usage can be read from the file the way the Claude
   Code adapter reads it.
2. **OpenCode.** Sessions are in a SQLite database with per-message token counts, which means a different
   reader (a database, plus a legacy JSON layout from older installs) before the same digest can be built.
3. **Aider.** Sessions are a markdown chat log, and token usage is a line of English in it, not a
   structured record; usage only exists as JSON if the user ran Aider with an analytics log. An adapter
   would have to parse prose or require that flag, so it comes last.

Other agents have been looked at (Gemini CLI, Copilot CLI, Cline; see [docs/ADAPTERS.md](docs/ADAPTERS.md))
but none is scheduled. If one of them matters to you, an
[adapter request](https://github.com/gaurav-gandhi-2411/tracegauge/issues/new?template=adapter_request.yml)
that describes the shape of its files (never their content) is the most useful thing you can send.

Alongside the adapters: the `tes` names are removed in 0.18, as announced in the 0.16.0 entry.

## Explicitly out of scope

- **A hosted or SaaS version.** Scoring stays on your machine.
- **Telemetry.** No phone-home, no usage analytics. The only network paths are opt-in and described in
  [PRIVACY.md](PRIVACY.md).
- **Paid APIs as a requirement.** The default path needs no key and no account; an API judge exists only
  as an opt-in you consent to per session.
- **A VS Code extension.**

If you need one of these, a fork is the honest answer.
