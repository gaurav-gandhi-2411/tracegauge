# Data sources

Every external (non-self-collected) dataset used anywhere in this project, its
license, and what was derived from it. Added in response to a real,
previously-uncredited attribution gap — see the entry below.

## SWE-chat (SALT-NLP/SWE-chat)

> Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat),
> which is made available under the [Open Data Commons Attribution License (ODC-BY) 1.0](https://opendatacommons.org/licenses/by/1-0/).

- **What it is:** a public corpus of real coding-agent sessions collected from
  real developers by the Entire.io CLI checkpoint logger.
- **Citation:** Baumann et al., 2026, arXiv:2604.20779.
- **License:** ODC-BY 1.0. Under §4.3 ("Produced Works"), publishing aggregate
  statistics computed from the data — not just redistributing the data itself —
  requires this notice. Verified directly against the license text
  (opendatacommons.org/licenses/by/1-0/) rather than assumed.
- **Context percentiles (0.17):** `tracegauge/data/multidev_bands.json` is a percentile table of tokens per
  turn per task type, built from the Claude Code sessions of SWE-chat v2 (495 developers, 8,367 sessions),
  developer-weighted, with no developer id, session id, text or per-developer value; a cell with fewer than
  5 developers is not published. It is a Produced Work under §4.3 and carries the notice next to every use
  (the context line, `docs/MULTIDEV_REFERENCE.md`, the README). Method: `docs/MULTIDEV_REFERENCE.md`.
- **What was derived from it:** the B5 generalization validation
  (`research/11-generalization.md`) — the 1,053 SWE-chat CC sessions (58 attributed developers plus 281 sessions with no
  user_id; see the correction below) and the 1.4% (SWE-chat) vs. 6.6% (calibration
  pool) REPEATED-FAILED-RETRY rate comparison (the maintainer's unpublished local measurement) reported in README.md and
  `research/09-cross-model.md`/`research/10-deterministic-waste.md`.
- **Correction (2026-10):** earlier releases and research notes said "172 independent developers,
  1,053 sessions". That mixed two counts. sessions.parquet lists 4,852 Claude Code sessions from 172 attributed developers (1,660 of those sessions have no user_id); only 1,053 have a raw transcript, from 58 attributed developers plus 281 sessions with no user_id. The detector rates and conclusions are
  unchanged; only the developer count they were attributed to was wrong.
- **Redistribution:** none. The raw SWE-chat data (`data/swechat_raw/`) and both
  adapted forms (`data/swechat_cc_adapted.jsonl`, `data/swechat_noncc_adapted.jsonl`), and
  the per-session detector signals derived from them (`data/public_waste_signals.jsonl`, keyed
  by SWE-chat session ids) are gitignored and never committed to this repo. The signals file is
  not distributed: regenerate it locally with `python scripts/generalization_run.py` (it
  downloads SWE-chat from Hugging Face, adapts it and re-runs the detectors; `--signals-out`
  chooses where it is written, and `scripts/diagnose_rfr_rate.py --swechat-signals` and
  `scripts/fix_noncc_signals.py --signals` read it back) — only the derived aggregate
  statistics are published, which is why this is purely a §4.3 (Produced Works)
  attribution question, not a §4.2 (Derivative Databases) redistribution question.

## Third-party trajectory datasets committed in the repository

These are in the git repository only. They are not in the PyPI wheel or sdist, and the Apache-2.0
grant on tracegauge's own code does **not** cover them: each remains under the licence its authors
chose, listed below. Licences were read from each dataset's own Hugging Face card (URL and retrieval
date given per entry), not assumed.

| Dataset | Licence (per its card) | Where in the repo | Redistributed? |
|---|---|---|---|
| nebius/SWE-agent-trajectories | CC-BY-4.0 | `data/validation-corpus/` (derived numeric labels and statistics for 80 sessions) | no: the traces were removed in 0.16.0; only derived numbers remain |
| nebius/SWE-rebench-openhands-trajectories | CC-BY-4.0 | `data/validation-corpus/` (derived numeric labels and statistics for 70 sessions) | no: the traces were removed in 0.16.0; only derived numbers remain |
| zai-org/CC-Bench-trajectories | MIT | `data/ood-corpus/` (5 traces) | yes, 5 sampled traces |
| synthetic OOD traces (generated for this project) | CC0 | `data/ood-corpus/` (5 traces, `source_dataset: synthetic_ood`) | yes, 5 traces; not third-party |
| SWE-Gym/OpenHands-Sampled-Trajectories | none stated | was `data/validation-corpus/` (50 corpus entries; traces never committed) | no: removed in 0.16.0 |
| SWE-Gym/SWE-Gym (task dataset, a different dataset from the trajectories above) | MIT | not committed; only instance ids appear in a taxonomy file (below) | no |
| SALT-NLP/SWE-chat | ODC-BY 1.0 | not committed | no (see above) |

### nebius/SWE-agent-trajectories

- **URL:** https://huggingface.co/datasets/nebius/SWE-agent-trajectories (card retrieved 2026-10-12)
- **Licence:** CC-BY-4.0 (card front matter `license: cc-by-4.0` and the card's "License" section).
- **Card caveats, repeated here because they travel with the data:** "please respect the license of
  each specific repository on which a particular instance is based" (the trajectories quote files
  from those repositories), and "if you intend to use the outputs of these models, you must comply
  with the Llama 3.1 License" (the trajectories were generated by Llama-3-family models).
- **What is used and how:** 80 trajectories, sampled and normalized by `scripts/00_download_corpus.py`
  into `data/validation-corpus/traces_normalized/` (that directory is git-ignored and no longer committed, see
  "What remains in the tree" below), with per-turn labels derived from them; used to validate the
  deterministic waste heuristics (`research/03-validation-corpus.md`).
- **Attribution:** Nebius, "SWE-agent trajectories", CC-BY-4.0
  (https://creativecommons.org/licenses/by/4.0/). Changes were made.

### nebius/SWE-rebench-openhands-trajectories

- **URL:** https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories (card retrieved
  2026-10-12). Instances come from https://huggingface.co/datasets/nebius/SWE-rebench.
- **Licence:** CC-BY-4.0 (card front matter `license: cc-by-4.0`). The trajectories were generated
  with Qwen3-Coder-480B-A35B-Instruct under OpenHands 0.54.0, per the card.
- **What is used and how:** 70 trajectories, sampled and normalized as above, used for the same
  heuristic validation. Modified (reformatted, truncated to the fields tracegauge reads).
- **Attribution (the card's requested citation):** Trofimova, Shevtsov, Badertdinov, Pyaev, Karasik,
  Golubev, "OpenHands Trajectories with Qwen3-Coder-480B-A35B-Instruct", Nebius blog, 2025.
  CC-BY-4.0. Changes were made.

#### What remains in the tree from the two nebius datasets, and what was removed in 0.16.0

Inventory taken at the commit that removed them. Both datasets are CC-BY-4.0, which permits
redistribution with attribution; the maintainer chose not to ship the trace text anyway. What stays
is our own derived numeric data, attributed here (Nebius, CC-BY-4.0; changes were made: the numbers
are computed from the trajectories and no trajectory text is kept).

Removed (verbatim trajectory text, or prompts that embedded it):

| Path | What it held |
|---|---|
| `data/validation-corpus/traces_normalized/*.json` (150 files) | the normalized trajectories: every turn's text, tool calls and tool results, and the patch diff |
| `data/batch_requests/*.jsonl` (3 files, 5 requests each) | the annotation prompts, each embedding the turns of a trajectory |

Kept, with the text fields stripped (shape unchanged, text replaced by an empty string or the key
removed):

| Path | What was stripped | What stays |
|---|---|---|
| `data/layer1_outputs.jsonl` (150 records) | `digest.task_description` and every `digest.turns[].content_snippet` (the first 300 characters of the issue text and of each turn) | session id, domain, outcome, turn count, token counts, duplicate counts, cache rate, per-turn token counts |
| `data/validation-corpus/annotations/spotcheck_sample.json` (11 turns) | `content_text`, `tool_uses`, `prev_content`, `next_content` and the labeler's free-text `notes` | session id, scaffold, instance id, outcome, turn index, `has_tools`, the four boolean spot-check labels |
| `data/validation-corpus/annotations/gpt_oss/*.json`, `sonnet_iaa/*.json` | the one-sentence `llm_h1_reason` to `llm_h4_reason` strings (2,091 of them) | session id, per-turn boolean labels, waste categories, waste percentage, token counts, model name |

Kept unchanged because they hold only identifiers, labels and numbers:
`data/validation-corpus/annotations/structural_gt/`, `corpus_manifest.jsonl` (session id, public
instance id, scaffold, model, outcome, counts), `taxonomy/`, `difficulty/`, `heuristic_results/`
(the failure files list session id and turn index, no text), `skipped.jsonl`,
`data/objective_proxy.jsonl`, `data/gold/sample.json`, `data/calibration_sample.json`, and the
cost log in `data/`.

Reproducibility, stated honestly: the scripts that read the traces (`scripts/01_annotate_corpus.py`,
`02_validate_heuristics.py`, `02b_generate_structural_gt.py`, `03_task_taxonomy.py`,
`04_difficulty_analysis.py`, `07` to `10`) need `data/validation-corpus/traces_normalized/` to exist.
Run `python scripts/00_download_corpus.py` to re-create it from the public datasets (the sampling
seed is fixed at 42, but the dataset snapshot Hugging Face serves today may differ from the one
sampled in May 2026, so a re-created sample is not guaranteed to be the same 150 sessions). The
published heuristic precision and recall figures are the maintainer's measurements from the
original run and cannot be re-derived from the committed files alone. No test in `tests/` reads
these directories.

### zai-org/CC-Bench-trajectories

- **URL:** https://huggingface.co/datasets/zai-org/CC-Bench-trajectories (card retrieved 2026-10-12)
- **Licence:** MIT (card front matter `license: mit`). The card names no copyright holder or year.
  Earlier copies of the five normalized files in `data/ood-corpus/traces_normalized/` and of
  `scripts/05_download_ood_traces.py` labelled it CC-BY-4.0; that was wrong and is corrected in 0.16.0.
- **What is used and how:** 5 Claude Code trajectories (model GLM-4.5), normalized by
  `scripts/05_download_ood_traces.py`, used as an out-of-distribution check (`scripts/06_evaluate_ood.py`).
  Modified (reformatted).
- **Attribution:** "CC-Bench trajectories", zai-org, MIT licence. The MIT permission notice, which
  the licence requires to accompany copies, is:

  > Permission is hereby granted, free of charge, to any person obtaining a copy of this software
  > and associated documentation files (the "Software"), to deal in the Software without
  > restriction, including without limitation the rights to use, copy, modify, merge, publish,
  > distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
  > Software is furnished to do so, subject to the following conditions: The above copyright notice
  > and this permission notice shall be included in all copies or substantial portions of the
  > Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
  > INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE
  > AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
  > DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  > OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
  >
  > (The card names no copyright holder or year; the holder is the dataset publisher, zai-org.)

### SWE-Gym/OpenHands-Sampled-Trajectories (removed)

- **URL:** https://huggingface.co/datasets/SWE-Gym/OpenHands-Sampled-Trajectories (card retrieved
  2026-10-12). The card and the Hugging Face metadata state no licence.
- **Decision:** with no stated licence the traces cannot be redistributed under Apache-2.0, so the 50
  corpus entries and everything that embeds their text were removed from the tree in 0.16.0. The trace
  files themselves were gitignored and never committed; the 50 manifest rows and the annotation and
  heuristic-output files remain in git history (for example at the `v0.15.0` tag). Per-session numeric scores keyed by session id
  (our own measurements, no trace text) were kept so published figures stay traceable.
- Downloading it yourself with `scripts/00_download_corpus.py` is your own use under the dataset's
  terms; nothing is redistributed from it.
- **What derived material remains, stated plainly:** only numbers, labels and public instance ids.
  (1) `data/judge_scores.jsonl`, `data/judge_scores_v1.jsonl` and
  `data/llm_provisional_ratings.jsonl` keep, per session id, the judge's numeric score, verdict,
  waste categories and confidence, and the rater's 1-5 rating. The one-sentence free-text
  `reasoning` and `justification` fields our own judge and rater wrote about those sessions (they
  paraphrased trace content) were removed in 0.16.0, for the SWE-Gym sessions and for the nebius
  sessions that share the files. (2) `data/validation-corpus/taxonomy/task_taxonomy.json` and
  `difficulty/difficulty_analysis.json` hold SWE-Gym instance ids (public identifiers, from the
  separate MIT-licensed SWE-Gym/SWE-Gym task dataset) and patch statistics, with no free text.
  (3) `data/objective_proxy.jsonl`, `data/gold/sample.json`, `data/calibration_sample.json`, the
  cost log holds session ids and counts. A verbatim eight-word SWE-Gym quote and
  the per-trace paraphrases in `research/03-validation-corpus.md` were removed earlier; only
  counts remain there. Sweep scope for this removal: every tracked `.json` and `.jsonl` file outside
  the package directories was scanned for string values of 60 or more characters, and every tracked file was searched for the 50 SWE-Gym session ids and
  for the words swegym and SWE-Gym; the aggregate description of the "empty-loop" failure cluster
  in `research/06-calibration.md` (14 sessions, counts only) was judged a statistic, not a
  paraphrase, and was kept.

### Other datasets referenced by scripts but not in the tree

`scripts/05_download_ood_traces.py` can also download AlienKevin/SWE-ZERO-12M-trajectories (Apache-2.0 per its
Hugging Face card; the script's recorded value used to say MIT and was corrected) and Agent-Ark/Toucan-1.5M (Apache-2.0 per its Hugging Face metadata). No trace from either is
committed.

## Fonts bundled in the PyPI package

Five WOFF2 files in `tracegauge/web/static/fonts/` are third-party, licensed under the SIL Open Font
License 1.1 (full texts next to the fonts, copyright lines and the modification statement in
`NOTICE`). Four are renamed Latin subsets of IBM Plex Sans/Mono (copyright IBM Corp.; renamed to
"TG Sans"/"TG Mono" because OFL-1.1 clause 3 reserves the name "Plex" for unmodified copies). One is
a Latin subset of Space Grotesk (copyright The Space Grotesk Project Authors).

The logo artwork (`tracegauge/web/static/brand/lockup-dark.svg`, `assets/brand/lockup*.svg`, the
social-preview card) also uses outlines of Sora (copyright 2019 The Sora Project Authors, SIL Open
Font License 1.1) converted to SVG paths. No Sora font file is bundled and the SVGs contain no
`<text>`; the licence text is `tracegauge/web/static/brand/OFL-Sora.txt` and
`assets/brand/OFL-Sora.txt`, and `NOTICE` carries the attribution.

## Open-weight models used for local/offline inference

Listed for transparency (LLM usage is documented, not hidden) — none of
these carry an attribution or redistribution obligation for how this project
uses them (local inference only, publishing aggregate results, no weight
redistribution or fine-tuned-derivative distribution).

| Model | Role | License | Obligation for this project's usage |
|---|---|---|---|
| Qwen3-30B-A3B | Trajectory judge (`tracegauge score --judge`, local Ollama) | Apache 2.0 | None — permissive, no redistribution here |
| Gemma 3 27B | Cross-model corroboration check (`research/09-cross-model.md`) | Gemma Terms of Use | None — §3.1's distribution clause only triggers on redistributing the weights or a derivative model; not triggered by local inference + publishing results |
| gpt-oss-120b | Groq free-tier judge path | Apache 2.0 | None — permissive, no redistribution here |

## Self-collected data (no third-party licensing question)

- The B1-B4 calibration corpus (181 of the author's own Claude Code sessions, formerly
  `data/corpus_pool/pool_adapted.jsonl`) is no longer in the repository; figures derived from it are
  the maintainer's unpublished local measurement. Not third-party data.
- Anthropic model pricing (`tracegauge/data/prices.json`) — public list prices, not a
  licensed dataset; each active entry carries its own `source_url`/`as_of` for
  the staleness guard (see the file's own `note` field for the full convention,
  including why retired entries are exempt).
