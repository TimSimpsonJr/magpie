"""ASCII only. Shared tiny own-corpus dataset emitter for the crossref smoke (CI +
local). Writes <data_dir>/{entities.ftm.json, manifest.yml} via the SAME render
path the entity-crossref skill uses, so CI exercises the real version-stamped
manifest flow. Run: python -m tests.helpers.emit_smoke_dataset"""
from __future__ import annotations
import pathlib
from scripts.entity_resolved_snapshot import ResolvedEntity, build_snapshot
from scripts.entity_yente_dataset import (
    DATASET_NAME, DatasetEntry, render_manifest, write_dataset,
)

# Known smoke entities -- the live test queries these exact ids/names.
SMOKE_CANONICAL_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80911"
SMOKE_NAME = "Jonathan Edward Maple"

def build_smoke_snapshot() -> dict:
    ents = [
        ResolvedEntity(
            canonical_id=SMOKE_CANONICAL_ID, schema="Person",
            caption=SMOKE_NAME, properties={"name": [SMOKE_NAME], "country": ["us"]},
        ),
        ResolvedEntity(
            canonical_id="b9c8d7e6f5a40312233445566778899aabbccddee"[:40], schema="Organization",
            caption="Riverside Holdings LLC", properties={"name": ["Riverside Holdings LLC"]},
        ),
    ]
    return build_snapshot(ents, [], [], investigation_id="smoke-inv",
                          algorithm="logic-v2", thresholds={}, generated_at="2026-06-07")

def emit(data_dir: str = "data/magpie_corpus") -> dict:
    out = pathlib.Path(data_dir)
    res = write_dataset(build_smoke_snapshot(), out, name=DATASET_NAME)
    entry = DatasetEntry(name=res["name"], title="Magpie smoke corpus",
                         path="/data/entities.ftm.json", version=res["version"])
    (out / "manifest.yml").write_text(render_manifest([entry]), encoding="utf-8")
    return res

if __name__ == "__main__":
    r = emit()
    print("emitted %d entities, version %s -> %s" % (r["count"], r["version"], r["entities_path"]))
