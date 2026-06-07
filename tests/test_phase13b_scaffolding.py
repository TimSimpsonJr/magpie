import pathlib, tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent

def test_yente_marker_registered():
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    markers = "\n".join(cfg["tool"]["pytest"]["ini_options"]["markers"])
    assert "yente:" in markers

def test_offline_exclusion_excludes_yente():
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "not yente" in ci

def test_requirements_crossref_present():
    req = (REPO / "requirements-crossref.txt").read_text(encoding="utf-8")
    assert "httpx" in req and "mcp" in req

def test_env_example_has_opensearch_and_update_token():
    env = (REPO / "infra/.env.example").read_text(encoding="utf-8")
    assert "OPENSEARCH_ADMIN_PASSWORD" in env and "YENTE_UPDATE_TOKEN" in env

def test_gitignore_blocks_own_corpus_data():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/data/" in gi or "data/magpie_corpus/" in gi
