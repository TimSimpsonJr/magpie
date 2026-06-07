import pathlib
REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills/entity-crossref/SKILL.md"

def test_skill_frontmatter_name():
    t = SKILL.read_text(encoding="utf-8")
    assert t.startswith("---")
    assert "name: entity-crossref" in t

def test_skill_is_ascii():
    t = SKILL.read_text(encoding="utf-8")
    assert t.isascii(), "SKILL.md must be ASCII-only"

def test_skill_marks_watchlist_optin_and_ccbync():
    t = SKILL.read_text(encoding="utf-8").lower()
    assert "opt-in" in t
    assert "cc-by-nc" in t

def test_skill_documents_deterministic_reindex():
    t = SKILL.read_text(encoding="utf-8")
    assert "AUTO_REINDEX" in t
    assert "yente reindex" in t

def test_skill_layer2_no_journalist_docker():
    t = SKILL.read_text(encoding="utf-8").lower()
    assert "layer-2" in t or "layer 2" in t
    assert "operator" in t
    # the journalist onramp stays docker-free
    assert "journalist" in t

def test_skill_references_the_scripts():
    t = SKILL.read_text(encoding="utf-8")
    for mod in ("entity_yente_dataset", "entity_crossref", "entity_yente_client", "yente_mcp_server"):
        assert mod in t

def test_prior_art_exists_and_ascii():
    pa = REPO / "skills/entity-crossref/references/prior-art.md"
    t = pa.read_text(encoding="utf-8")
    assert t.isascii()
    assert "5.4.0" in t and "2.19.5" in t
