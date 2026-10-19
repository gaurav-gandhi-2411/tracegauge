# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""scripts/check_price_table_vs_vendor.py — fetches Anthropic's own published pricing
page and compares it, in BOTH directions, against this repo's price table
(tracegauge/data/prices.json).

History: this check originally looped over the table's own keys, filtered through a
hand-maintained display-name map, and compared only input/output. So a model the vendor page
lists but the table lacks (claude-opus-5-5, claude-sonnet-5-5: 84,140 unpriced turns in the
author's own transcripts) was structurally undetectable, and the cache columns (0.05x / 0.025x
cache hits on the 5.x models) were never compared. This version derives the model list from the
PAGE and fails when:

(a) the page lists a model whose key is absent from the table (unless it is in
    IGNORED_PAGE_KEYS, each with a written reason);
(b) any of input / output / 5m write / 1h write / cache-hit disagrees with the page (cache
    columns are compared against the table's own multipliers, per-model overrides included);
(c) a non-retired table entry is no longer on the page;
(d) a tiered model (Haiku 5.5's "for prompts up to/over N tokens" rows) disagrees with the
    table's ``long_context`` block, or the table has/lacks a tier the page lacks/has;
(e) the page's "Fast mode pricing" table disagrees with the table's per-model
    ``fast_input_usd_per_mtok`` / ``fast_output_usd_per_mtok`` (a model with fast rates on only
    one side is a mismatch);
(f) the page's "Data residency pricing" rule ("Claude X.Y and later" models get an Nx multiplier
    for ``inference_geo: "us"``) disagrees with which table models carry ``geo_us_multiplier``,
    or with its value -- eligibility is derived from the model key's version, not hard-coded;
(g) the page's web-search price ("$N per 1,000 searches") disagrees with
    ``server_tool_rates.web_search_usd_per_1000``.
A page that lacks any of those three sections (or has an ambiguous one) is COULD NOT VERIFY.

TWO DISTINCT FAILURE MODES, never conflated:
1. COULD NOT VERIFY -- the page could not be fetched or parsed, or parsed to zero models, or a
   row had unreadable prices. Fails CLOSED: "we do not know" is never a pass.
2. MISMATCH / MISSING -- the page was parsed and the table disagrees with it.

The model key is DERIVED from the display name ("Claude Opus 5.5" -> "claude-opus-5-5"), not
looked up in a hard-coded map, so a new model on the page is found without editing this file.

The page is Anthropic's markdown export,
https://platform.claude.com/docs/en/about-claude/pricing.md (simpler and sturdier to parse than
the HTML). Plain stdlib HTTP GET, no paid API, no new dependency. ``--page FILE`` checks a
saved copy instead of fetching (used by the tests and to reproduce a past miss); ``--prices
FILE`` checks a table other than the bundled one.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tracegauge.cost import cache_read_multiplier, load_price_table  # noqa: E402

_USER_AGENT = "Mozilla/5.0 (compatible; tracegauge-price-vendor-check/2.0)"
_TIMEOUT_SECONDS = 20

#: Always check the bundled table, never a ~/.tes/prices.json or TRACEGAUGE_PRICE_TABLE override.
_BUNDLED_PRICES = _ROOT / "tracegauge" / "data" / "prices.json"

ANTHROPIC_MD_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"

#: Page models deliberately NOT required in the table: ``{derived_key: reason}``. Empty on
#: purpose -- every model the page lists is currently priced (retired ones included, so old
#: transcripts still price). Add an entry only with a written reason; a bare skip recreates the
#: blind spot this script exists to close.
IGNORED_PAGE_KEYS: dict[str, str] = {}

#: Float noise tolerance when comparing a computed table rate with the page's printed price.
_EPS = 1e-9


class FetchError(Exception):
    """The vendor page could not be fetched or parsed as expected -- see the module docstring's
    "TWO DISTINCT FAILURE MODES"."""


@dataclass(frozen=True)
class VendorRow:
    """One priced row of the page's '## Model pricing' table (USD per MTok)."""

    display_name: str
    model_key: str
    tier: str  # "base" or "long" (Haiku 5.5's "over N tokens" row)
    threshold: int | None  # prompt-token threshold named in a tiered row's label, else None
    input: float
    write_5m: float
    write_1h: float
    cache_read: float
    output: float


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status != 200:
                raise FetchError(f"{url}: HTTP {status}")
            body: bytes = resp.read()
            return body.decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise FetchError(f"{url}: {e}") from e
    except TimeoutError as e:
        raise FetchError(f"{url}: timed out after {_TIMEOUT_SECONDS}s") from e


def model_key_from_display_name(display_name: str) -> str:
    """'Claude Opus 5.5' -> 'claude-opus-5-5' (lowercase, non-alphanumerics to '-')."""
    return re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")


# "(?:USD\s*)?": the fast-mode table on the live page writes "$8 USD / MTok" (seen 2026-10-19); the
# model table still writes "$5 / MTok". Both must parse; anything else still fails closed.
_PRICE_RE = re.compile(r"\$\s*([\d,]*\.?\d+)\s*(?:USD\s*)?/\s*MTok")
_TIER_RE = re.compile(r"for prompts\s+(up to|over)\s+([\d,]+)\s+tokens", re.IGNORECASE)

# Header cell (lowercased) substring -> VendorRow field. Matched by NAME, not position, so a
# reordered or extended table still parses correctly or fails loudly.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("base input", "input"),
    ("5m cache", "write_5m"),
    ("1h cache", "write_1h"),
    ("cache hit", "cache_read"),
    ("output", "output"),
)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_vendor_table(md: str) -> list[VendorRow]:
    """Every priced row of the '## Model pricing' table.

    Raises FetchError (never returns a partial or empty result) when the section or its header
    is missing, a required column is absent, a data row has an unreadable price, a tier label is
    not understood, or the table yields no rows -- a parse we cannot trust must not pass.
    """
    idx = md.find("## Model pricing")
    if idx == -1:
        raise FetchError("'## Model pricing' section not found")
    column_index: dict[str, int] | None = None
    rows: list[VendorRow] = []
    for line in md[idx:].splitlines()[1:]:
        if line.startswith("#"):
            break  # next section -- do not read past our table
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if set("".join(cells)) <= {"-", ":", " "}:
            continue  # the |---|---| separator row
        if column_index is None:
            if cells[0].lower() != "model":
                continue
            lowered = [c.lower() for c in cells]
            column_index = {}
            for needle, field_name in _COLUMNS:
                matches = [i for i, c in enumerate(lowered) if needle in c]
                if not matches:
                    raise FetchError(f"pricing table has no '{needle}' column: {cells}")
                column_index[field_name] = matches[0]
            continue
        rows.append(_parse_row(cells, column_index))
    if column_index is None:
        raise FetchError("pricing table header row (first cell 'Model') not found")
    if not rows:
        raise FetchError("pricing table parsed to zero models")
    return rows


def _parse_row(cells: list[str], column_index: dict[str, int]) -> VendorRow:
    if len(cells) <= max(column_index.values()):
        raise FetchError(f"pricing row has too few cells: {cells}")
    label = cells[0]
    display_name = label.split("(", 1)[0].strip()
    if not display_name:
        raise FetchError(f"pricing row has no model name: {cells}")
    tier, threshold = "base", None
    qualifier = label[len(display_name) :]
    if re.search(r"for prompts", qualifier, re.IGNORECASE):
        match = _TIER_RE.search(qualifier)
        if match is None:
            raise FetchError(f"unrecognised prompt-tier label: {label!r}")
        tier = "long" if match.group(1).lower() == "over" else "base"
        threshold = int(match.group(2).replace(",", ""))
    values: dict[str, float] = {}
    for field_name, col in column_index.items():
        price = _PRICE_RE.search(cells[col])
        if price is None:
            raise FetchError(f"{display_name!r}: unreadable {field_name} price {cells[col]!r}")
        values[field_name] = float(price.group(1).replace(",", ""))
    return VendorRow(
        display_name=display_name,
        model_key=model_key_from_display_name(display_name),
        tier=tier,
        threshold=threshold,
        **values,
    )


def parse_anthropic_markdown(md: str) -> dict[str, tuple[float, float]]:
    """{display_name: (input, output)} for base-tier rows. Kept for callers of the pre-v2 API;
    unlike v2's parse_vendor_table it returns {} instead of raising when there is no table."""
    try:
        rows = parse_vendor_table(md)
    except FetchError:
        return {}
    return {r.display_name: (r.input, r.output) for r in rows if r.tier == "base"}


def _differs(ours: float, theirs: float) -> bool:
    return abs(ours - theirs) > _EPS


def _compare_rates(
    label: str,
    row: VendorRow,
    rates: dict[str, Any],
    read_mult: float,
    prices: dict[str, Any],
    problems: list[str],
) -> None:
    """Append one problem per column where the table's derived rate disagrees with ``row``."""
    in_rate = float(rates["input_usd_per_mtok"])
    out_rate = float(rates["output_usd_per_mtok"])
    mults = prices["cache_multipliers"]
    expected = {
        "input": in_rate,
        "output": out_rate,
        "5m cache write": in_rate * float(mults["write_5min"]),
        "1h cache write": in_rate * float(mults["write_1hr"]),
        "cache hit": in_rate * read_mult,
    }
    theirs = {
        "input": row.input,
        "output": row.output,
        "5m cache write": row.write_5m,
        "1h cache write": row.write_1h,
        "cache hit": row.cache_read,
    }
    for column, ours_value in expected.items():
        if _differs(ours_value, theirs[column]):
            problems.append(
                f"{label}: {column} ours=${ours_value:g} vs page=${theirs[column]:g} per MTok"
            )


def compare_table(
    prices: dict[str, Any],
    rows: list[VendorRow],
    ignored: dict[str, str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Compare ``prices`` with the parsed page. Returns ``(missing, mismatches, stale)``:

    * missing    -- page models (or tiers) absent from the table (case a / d)
    * mismatches -- any rate column that disagrees (case b / d)
    * stale      -- non-retired table entries the page no longer lists (case c)
    """
    skip = IGNORED_PAGE_KEYS if ignored is None else ignored
    models: dict[str, dict[str, Any]] = prices.get("models", {})
    missing: list[str] = []
    mismatches: list[str] = []
    seen: set[str] = set()
    tiers_on_page: dict[str, set[str]] = {}
    for row in rows:
        if row.model_key in skip:
            continue
        seen.add(row.model_key)
        tiers_on_page.setdefault(row.model_key, set()).add(row.tier)
        entry = models.get(row.model_key)
        if entry is None:
            missing.append(
                f"{row.model_key}: page lists '{row.display_name}' "
                f"(input ${row.input:g}, output ${row.output:g}, cache hit ${row.cache_read:g} "
                "per MTok) but prices.json has no such key"
            )
            continue
        read_mult = cache_read_multiplier(entry, prices)
        if row.tier == "long":
            tier = entry.get("long_context")
            if not isinstance(tier, dict):
                missing.append(
                    f"{row.model_key}: page lists a prompts-over-{row.threshold} tier, "
                    "prices.json has no long_context block"
                )
                continue
            if row.threshold != int(tier["threshold_prompt_tokens"]):
                mismatches.append(
                    f"{row.model_key}: long_context threshold ours="
                    f"{tier['threshold_prompt_tokens']} vs page={row.threshold}"
                )
            _compare_rates(f"{row.model_key} (long)", row, tier, read_mult, prices, mismatches)
        else:
            _compare_rates(row.model_key, row, entry, read_mult, prices, mismatches)
    stale: list[str] = []
    for key, entry in models.items():
        if entry.get("retired") or key in skip:
            continue
        if key not in seen:
            stale.append(f"{key}: non-retired entry is no longer listed on the page")
        elif isinstance(entry.get("long_context"), dict) and "long" not in tiers_on_page[key]:
            stale.append(f"{key}: has a long_context tier the page no longer lists")
    return missing, mismatches, stale


@dataclass(frozen=True)
class VendorModifiers:
    """The page's billing modifiers beyond the per-model token table."""

    web_search_per_1000: float
    fast: dict[str, tuple[float, float]]  # model key -> (input, output) USD per MTok
    geo_us_multiplier: float
    geo_min_version: tuple[int, int]  # "Claude X.Y and later models"


def _section(md: str, heading: str) -> str:
    """Text of the section whose heading line contains ``heading``, up to the next heading."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#") and heading in line:
            body: list[str] = []
            for nxt in lines[i + 1 :]:
                if nxt.startswith("#"):
                    break
                body.append(nxt)
            return "\n".join(body)
    raise FetchError(f"'{heading}' section not found")


def parse_vendor_modifiers(md: str) -> VendorModifiers:
    """Web-search price, fast-mode rates and the data-residency rule from the page.

    Raises FetchError (never returns defaults) when a section is missing, a number is
    unreadable or the page states the web-search price inconsistently.
    """
    prices_found = {
        m.group(1).replace(",", "")
        for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)\s+per\s+1,000\s+searches", md)
    }
    if len(prices_found) != 1:
        raise FetchError(f"web search price not found exactly once: {sorted(prices_found)}")

    fast: dict[str, tuple[float, float]] = {}
    column_index: dict[str, int] | None = None
    for line in _section(md, "Fast mode pricing").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if set("".join(cells)) <= {"-", ":", " "}:
            continue
        if column_index is None:
            lowered = [c.lower() for c in cells]
            if lowered[0] != "model" or "input" not in " ".join(lowered):
                continue
            try:
                column_index = {
                    "input": next(i for i, c in enumerate(lowered) if "input" in c),
                    "output": next(i for i, c in enumerate(lowered) if "output" in c),
                }
            except StopIteration as e:
                raise FetchError(f"fast mode table lacks an input/output column: {cells}") from e
            continue
        rates: list[float] = []
        for field_name in ("input", "output"):
            price = _PRICE_RE.search(cells[column_index[field_name]])
            if price is None:
                raise FetchError(f"fast mode row {cells!r}: unreadable {field_name} price")
            rates.append(float(price.group(1).replace(",", "")))
        for name in cells[0].split(" / "):  # "Claude Opus 5 / Claude Opus 4.8" share a row
            if name.strip():
                fast[model_key_from_display_name(name)] = (rates[0], rates[1])
    if column_index is None or not fast:
        raise FetchError("fast mode pricing table not found or empty")

    residency = _section(md, "Data residency pricing")
    version = re.search(r"Claude\s+(\d+)\.(\d+)\s+and later", residency)
    mult = re.search(r"([\d.]+)x\s+multiplier", residency)
    if version is None or mult is None:
        raise FetchError("data residency rule ('Claude X.Y and later', 'Nx multiplier') not found")
    return VendorModifiers(
        web_search_per_1000=float(prices_found.pop()),
        fast=fast,
        geo_us_multiplier=float(mult.group(1)),
        geo_min_version=(int(version.group(1)), int(version.group(2))),
    )


_VERSION_KEY_RES = (
    re.compile(r"^claude-(?:opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d+))?$"),  # claude-opus-4-6
    re.compile(r"^claude-(\d+)(?:-(\d+))?-(?:opus|sonnet|haiku)$"),  # claude-3-5-sonnet
)


def model_version(model_key: str) -> tuple[int, int] | None:
    """(major, minor) of a model key, or None when the key has no readable version."""
    for pattern in _VERSION_KEY_RES:
        m = pattern.match(model_key)
        if m:
            return int(m.group(1)), int(m.group(2) or 0)
    return None


def compare_modifiers(prices: dict[str, Any], mods: VendorModifiers) -> tuple[list[str], list[str]]:
    """Compare web search, fast mode and the geo multiplier. Returns ``(missing, mismatches)``."""
    models: dict[str, dict[str, Any]] = prices.get("models", {})
    missing: list[str] = []
    mismatches: list[str] = []

    rates = prices.get("server_tool_rates")
    ours = rates.get("web_search_usd_per_1000") if isinstance(rates, dict) else None
    if not isinstance(ours, int | float) or _differs(float(ours), mods.web_search_per_1000):
        mismatches.append(
            f"web search: ours={ours!r} vs page=${mods.web_search_per_1000:g} per 1,000 searches"
        )

    for key, (fin, fout) in sorted(mods.fast.items()):
        entry = models.get(key)
        if entry is None:
            missing.append(f"{key}: page lists fast-mode rates but prices.json has no such key")
            continue
        for label, field_name, theirs in (
            ("input", "fast_input_usd_per_mtok", fin),
            ("output", "fast_output_usd_per_mtok", fout),
        ):
            value = entry.get(field_name)
            if not isinstance(value, int | float) or _differs(float(value), theirs):
                mismatches.append(
                    f"{key}: fast {label} ours={value!r} vs page=${theirs:g} per MTok"
                )
    for key, entry in sorted(models.items()):
        if key not in mods.fast and (
            "fast_input_usd_per_mtok" in entry or "fast_output_usd_per_mtok" in entry
        ):
            mismatches.append(f"{key}: has fast-mode rates the page does not list for it")

    for key, entry in sorted(models.items()):
        version = model_version(key)
        if version is None:
            mismatches.append(f"{key}: cannot derive a model version to decide geo eligibility")
            continue
        eligible = version >= mods.geo_min_version
        ours_mult = entry.get("geo_us_multiplier")
        if eligible and (
            not isinstance(ours_mult, int | float)
            or _differs(float(ours_mult), mods.geo_us_multiplier)
        ):
            mismatches.append(
                f"{key}: geo_us_multiplier ours={ours_mult!r} vs page={mods.geo_us_multiplier:g}x "
                f"(Claude {mods.geo_min_version[0]}.{mods.geo_min_version[1]} and later)"
            )
        if not eligible and ours_mult is not None:
            mismatches.append(
                f"{key}: has geo_us_multiplier but the page limits it to Claude "
                f"{mods.geo_min_version[0]}.{mods.geo_min_version[1]} and later"
            )
    return missing, mismatches


def _report(title: str, items: list[str]) -> None:
    if items:
        print(f"{title}:", file=sys.stderr)
        for item in items:
            print(f"  - {item}", file=sys.stderr)
        print(file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare prices.json with Anthropic's page.")
    parser.add_argument("--page", type=Path, help="check a saved pricing.md instead of fetching")
    parser.add_argument("--prices", type=Path, help="check this prices.json, not the bundled one")
    args = parser.parse_args(argv)

    prices = load_price_table(args.prices or _BUNDLED_PRICES)
    try:
        md = args.page.read_text(encoding="utf-8") if args.page else _fetch(ANTHROPIC_MD_URL)
        rows = parse_vendor_table(md)
        mods = parse_vendor_modifiers(md)
    except (FetchError, OSError) as e:
        print(
            "COULD NOT VERIFY (fetch/parse failure, not a mismatch -- failing closed):",
            file=sys.stderr,
        )
        print(f"  - {e}", file=sys.stderr)
        return 1

    missing, mismatches, stale = compare_table(prices, rows)
    mod_missing, mod_mismatches = compare_modifiers(prices, mods)
    missing += mod_missing
    mismatches += mod_mismatches
    if not (missing or mismatches or stale):
        print(
            f"OK: {len(rows)} page rows verified against the table "
            "(input, output, 5m/1h cache write, cache hit, tiers, fast-mode rates, "
            "inference_geo multiplier and eligibility, web search); no page model missing."
        )
        return 0
    _report("MISSING FROM prices.json (page lists a model/tier the table lacks)", missing)
    _report("PRICE MISMATCH (table disagrees with the page)", mismatches)
    _report("STALE ENTRY (table has a non-retired entry the page dropped)", stale)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
