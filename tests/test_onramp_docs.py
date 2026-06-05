"""Guards for the dual onramp: the journalist guide never mentions Docker, and
the operator guide stays Layer 0-1 (no Docker either)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPERATOR = ROOT / "docs" / "OPERATOR_GUIDE.md"
JOURNALIST = ROOT / "docs" / "JOURNALIST_START.md"
README = ROOT / "README.md"


def test_guides_exist():
    assert OPERATOR.is_file()
    assert JOURNALIST.is_file()


def test_journalist_guide_never_mentions_docker():
    assert "docker" not in JOURNALIST.read_text(encoding="utf-8").lower()


def test_operator_guide_is_layer_0_1_no_docker():
    low = OPERATOR.read_text(encoding="utf-8").lower()
    assert "docker" not in low
    assert "mise run bootstrap" in low                 # the real setup step
    assert "detect_tier" in low                        # the verify step


def test_journalist_guide_is_conversational_not_infra():
    low = JOURNALIST.read_text(encoding="utf-8").lower()
    assert "doctor" in low                              # points at the health check
    # journalist terms, not infra plumbing
    assert "pip install" not in low and "venv" not in low


def test_readme_routes_both_personas():
    low = README.read_text(encoding="utf-8").lower()
    assert "operator_guide.md" in low and "journalist_start.md" in low
    # the polished dual-onramp routes by SKILL (setup / doctor), not just by file,
    # and mentions the health check -- pins the main-thread README polish so the
    # test is not vacuously green against the pre-existing README
    assert "two onramps" in low
    assert "setup" in low and "doctor" in low
    assert "detect_tier" in low
