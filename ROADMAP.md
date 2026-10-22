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
   Code adapter reads it. This is the first adapter to follow 0.17.0.
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

## Measured, not shipped: two detector candidates

Two candidate detectors were built and measured for 0.18 against thresholds written down before any
measurement. Neither met its bar, so neither ships. The numbers are kept here so the work is not lost;
they are the maintainer's unpublished local measurements. The public sample is a random 2,500 (seed 42)
of the Claude Code sessions in [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat),
2,448 of them priced, 1,063 developer clusters. Contains information from SALT-NLP/SWE-chat, which is
made available under the [ODC Attribution License 1.0](https://opendatacommons.org/licenses/by/1-0/).

- **Context bloat:** a tool call after which the next request carried at least 10,000 new tokens, re-sent
  by every later request. 34.0% of the public sample had at least $0.50 of it (developer-cluster
  bootstrap 95% interval 31.2 to 37.0%), $2,671.86 of $46,335.57 API-equivalent spend (5.8%). That
  passed the prevalence bar (20%). The precision bar did not pass. Three judges from different model
  families (Claude Sonnet 5.5, gpt-oss-120b, Qwen3.8-27B), each admitted at 90% or better on calibration
  cards, read 60 findings with the tool call and its output. They answered whether a size-limited form
  of the call (head, grep, a ranged read, a summary) would plausibly have kept the needed information.
  A majority said yes for 17 of 60 (28.3%, session-cluster bootstrap 17.2 to 39.4%; bar 85%), and the
  judges agreed little with each other (Fleiss kappa 0.18; bar 0.6). Most large reads were judged to
  be needed. The token count is also loose: it is everything new in the next request, and in 15 of the
  60 findings it was more than five times the size of the tool output itself.
- **Model mix:** short turns on a top-tier model, priced as if they had run on Sonnet 5.5. This is a
  price difference only, with no quality claim, and an upper bound, because it assumes the cheaper
  model would find its cache warm. 49.1% of the public sample had at least $0.50 of it (46.1 to
  51.9%), $6,156.59 in total. Precision could not be measured: on calibration cards asking whether a
  mid-tier model could perform a turn's action, two of the three judges scored 50% and 80% (bar 90%),
  and no other model family was available to replace them. On one developer's sessions, 7 of 38
  (18.4%) had at least $0.50 of it.

Either could come back with a new design measured against the same bars: for context bloat, a token
count tied to the single tool result; for model mix, a way of judging that tells routine turns from
hard ones.

## Open questions from the 0.17.0 measurements

Two things the 0.17.0 numbers raised and did not settle. Neither changes what 0.17.0 does.

- **Task type and tokens per turn in the public data.** In the public SWE-chat sessions the task type
  explains almost none of the spread in tokens per turn within a developer (0.3% of the variance,
  developer-cluster bootstrap 95% interval 0.1 to 0.7%; see [docs/METHODOLOGY.md](docs/METHODOLOGY.md)),
  while in the maintainer's own sessions it explains about a third (the maintainer's unpublished local
  measurement, 45 sessions in 11 projects). Whether that is a property of the work, of how task types
  are assigned to the public transcripts, or of one person's small sample is not known. The first step
  is to test whether the task-type classifier separates sessions as well on the public transcripts as on
  local ones, before anyone draws a conclusion from the gap.
- **Using tokens per turn for the context line.** The context line already uses it because it was the
  most stable of three normalisers across developers
  ([docs/MULTIDEV_REFERENCE.md](docs/MULTIDEV_REFERENCE.md)). If the classifier work above shows that the public
  task types are noisy, the context line's per-type cells would need to be rebuilt, not just re-labelled.

## Explicitly out of scope

- **A hosted or SaaS version.** Scoring stays on your machine.
- **Telemetry.** No phone-home, no usage analytics. The only network paths are opt-in and described in
  [PRIVACY.md](PRIVACY.md).
- **Paid APIs as a requirement.** The default path needs no key and no account; an API judge exists only
  as an opt-in you consent to per session.
- **A VS Code extension.**

If you need one of these, a fork is the honest answer.
