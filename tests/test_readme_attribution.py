# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The SWE-chat attribution is a licence condition (ODC-BY 1.0, clause 4.3 for produced works), so it
must stay in README.md, whose text is also the PyPI project description."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DATASET = "https://huggingface.co/datasets/SALT-NLP/SWE-chat"
_LICENCE = "https://opendatacommons.org/licenses/by/1-0/"


def _methodology_section() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("\n## Methodology")
    nxt = text.find("\n## ", start + 1)
    return text[start : nxt if nxt != -1 else len(text)]


def test_readme_methodology_carries_the_swechat_notice_with_both_links() -> None:
    section = _methodology_section()
    assert "Contains information from [SALT-NLP/SWE-chat](" + _DATASET + ")" in section
    assert "ODC Attribution License 1.0](" + _LICENCE + ")" in section
    assert "made available under" in section


def test_the_notice_is_one_sentence_in_the_readme_and_not_only_elsewhere() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.count("Contains information from [SALT-NLP/SWE-chat]") == 1
