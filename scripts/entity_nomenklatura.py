"""ASCII only. Phase 13a resolution edge -- the ONLY nomenklatura importer.

Linux/CI-only: nomenklatura pulls followthemoney (PyICU/ICU has no Windows
wheel), so importing this module fails on the Windows dev venv -- by design. It
runs in the CI `ftm` job (Ubuntu) and is the ONLY real verification surface for
this module (its tests are `ftm`-marked and skip on Windows).

It drives the resolve -> human-review -> apply -> resolved-snapshot flow against
the real nomenklatura Resolver. The flow spans SEPARATE process invocations (the
human reviews the HTML packet offline between resolve and apply), so EVERYTHING
persists to scratch_dir and each entry point RELOADS from disk -- no in-memory
store is carried across the roundtrip. Only the resolver SQLite
(NOMENKLATURA_DB_URL) persists judgements; load_entity_file_store is per-path +
in-memory, so resolve records {entities_paths, config} to scratch/run.json and the
later steps reload the same inputs.

The pure cores (entity_resolution_policy / entity_review_packet /
entity_resolved_snapshot) are Windows-safe and imported normally; nomenklatura /
followthemoney are imported at module top (like entity_ftmize).
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from dataclasses import dataclass
from typing import Optional

from followthemoney import StatementEntity as Entity
from followthemoney import model
from nomenklatura.db import get_engine
from nomenklatura.judgement import Judgement
from nomenklatura.matching import LogicV2
from nomenklatura.resolver import Resolver
from nomenklatura.store import load_entity_file_store
from nomenklatura.xref import xref

from scripts import entity_resolved_snapshot
from scripts.entity_resolution_policy import (
    Candidate,
    CandidateSide,
    Mention,
    ResolutionConfig,
    canonical_id,
    edge_id,
)
from scripts.entity_review_packet import (
    build_candidate_snapshot,
    parse_verdicts,
)


# ---------------------------------------------------------------------------
# Public result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ResolveResult:
    """What resolve() persisted to scratch_dir."""

    candidate_snapshot_path: str
    packet_hash: str
    auto_merge_log_path: str


@dataclass
class ApplyResult:
    """The outcome of apply_verdicts() (fail-closed; design D6)."""

    applied: int
    skipped: int
    aborted_reason: Optional[str]


# ---------------------------------------------------------------------------
# Scratch-file names (one place so resolve / apply / build agree)
# ---------------------------------------------------------------------------

_RESOLVER_DB = "resolver.db"
_XREF_INDEX = "xref-index"
_AUTO_MERGE_LOG = "auto_merge_log.jsonl"
_CANDIDATE_SNAPSHOT = "candidate_snapshot.json"
_RUN_JSON = "run.json"
_COMBINED_ENTITIES = "combined.entities.ftm.json"

_AUTO_USER = "magpie-auto"
_REVIEW_USER = "magpie-review"


# ---------------------------------------------------------------------------
# Resolver + store plumbing
# ---------------------------------------------------------------------------

def _resolver_db_url(scratch_dir) -> str:
    """The sqlite:// URL for the per-investigation resolver DB under scratch_dir.

    Forward-slash / as_posix so the URL is well-formed on every platform.
    """
    db_path = pathlib.Path(scratch_dir) / _RESOLVER_DB
    return "sqlite:///" + db_path.as_posix()


def _open_resolver(scratch_dir):
    """Open a Resolver bound to the PER-INVESTIGATION sqlite DB under scratch_dir.

    nomenklatura reads NOMENKLATURA_DB_URL into settings.DB_URL at IMPORT time, so
    setting the env after this module is already imported has NO effect --
    make_default() would silently fall back to the global default
    ./nomenklatura.db (a design-D5 per-investigation-isolation violation AND a
    CWD leak). We therefore bind the scratch DB EXPLICITLY: build the engine for
    our scratch URL and hand it to make_default(), bypassing settings.DB_URL. The
    env is also set, for any code path that re-reads it.
    """
    db_url = _resolver_db_url(scratch_dir)
    os.environ["NOMENKLATURA_DB_URL"] = db_url
    return Resolver[Entity].make_default(get_engine(db_url))


def _load_store(entities_paths, resolver, *, scratch_dir=None):
    """Load the bundle entities into one combined in-memory store.

    load_entity_file_store(path, resolver, cleaned=True) reads newline-delimited
    FtM entity JSON (one proxy.to_dict() per line -- the entities.ftm.json
    entity_ftmize.write_bundle produced) and persists nothing itself. cleaned=True
    matches the cleaned=True endpoints entity_ftmize wrote.

    For a SINGLE bundle path we load it directly. For MULTIPLE paths we
    concatenate every bundle's lines into one combined entities file under
    scratch_dir and load THAT -- this combines the whole investigation corpus into
    one store using ONLY the verified single-path load_entity_file_store signature
    (no unverified store-merge kwarg). The combined file is deterministic
    (bundle order, then line order), so apply / build reload an identical store.
    """
    paths = [pathlib.Path(p) for p in entities_paths]
    if not paths:
        raise ValueError("_load_store requires at least one entities path")

    if len(paths) == 1:
        return load_entity_file_store(paths[0], resolver, cleaned=True)

    base = pathlib.Path(scratch_dir) if scratch_dir is not None else paths[0].parent
    combined = base / _COMBINED_ENTITIES
    # Write to a sibling temp path then os.replace so the published combined
    # bundle is ALWAYS complete -- an interrupt mid-write must not leave a corrupt
    # partial file for the next run to read.
    tmp = combined.with_suffix(combined.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        for path in paths:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.write(line)
                    out.write("\n")
    os.replace(tmp, combined)
    return load_entity_file_store(combined, resolver, cleaned=True)


# ---------------------------------------------------------------------------
# Bundle / provenance reading (hydration source; Windows-safe JSON)
# ---------------------------------------------------------------------------

def _base_name_for(entities_path) -> str:
    """The <name> base of an entities path (sibling artifacts share this base).

    entity_ftmize names the pair <name>.entities.ftm.json /
    <name>.provenance.jsonl / <name>.manifest.json, so the base is the file name
    with the ".entities.ftm.json" suffix stripped. For a NON-standard name the
    fallback strips ".ftm.json" then a trailing ".entities" -- NOT pathlib `.stem`,
    which removes only ONE suffix (so "foo.entities.ftm.json" -> "foo.entities.ftm",
    wrong) and would mis-derive every sibling path.
    """
    name = pathlib.Path(entities_path).name
    suffix = ".entities.ftm.json"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    # Fallback for a non-standard name: strip ".ftm.json" then a ".entities" tail.
    if name.endswith(".ftm.json"):
        name = name[: -len(".ftm.json")]
    if name.endswith(".entities"):
        name = name[: -len(".entities")]
    return name


def _provenance_path_for(entities_path) -> pathlib.Path:
    """Derive the sibling <name>.provenance.jsonl path from an entities path."""
    p = pathlib.Path(entities_path)
    return p.with_name(_base_name_for(p) + ".provenance.jsonl")


def _read_entity_lines(entities_paths) -> dict:
    """Read every bundle entities file into {id: proxy_dict} (last write wins)."""
    out: dict = {}
    for ep in entities_paths:
        path = pathlib.Path(ep)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            out[obj["id"]] = obj
    return out


def _read_provenance(entities_paths) -> list:
    """Read every sibling provenance.jsonl into one list of rows (in file order)."""
    rows: list = []
    for ep in entities_paths:
        prov_path = _provenance_path_for(ep)
        if not prov_path.exists():
            continue
        for line in prov_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Candidate hydration (resolve + apply MUST hydrate identically)
# ---------------------------------------------------------------------------

def _proxy_name(proxy_dict: dict) -> str:
    """The first FtM `name` value of a proxy dict, or ""."""
    names = (proxy_dict.get("properties") or {}).get("name") or []
    return names[0] if names else ""


def _proxy_schema(proxy_dict: dict) -> str:
    return proxy_dict.get("schema", "")


def _proxy_aliases(proxy_dict: dict, caption: str) -> list:
    """All other `name` values (the captioned one removed once)."""
    names = list((proxy_dict.get("properties") or {}).get("name") or [])
    if caption in names:
        names.remove(caption)
    return names


def _mentions_for(node_id: str, provenance_rows: list) -> list:
    """Build the Mention list for a node id from provenance rows targeting it."""
    mentions = []
    for row in provenance_rows:
        if row.get("target_id") != node_id:
            continue
        mentions.append(
            Mention(
                doc_id=str(row.get("doc_id", "")),
                page=row.get("page", 0),
                char_start=row.get("char_start", 0),
                char_end=row.get("char_end", 0),
                text=str(row.get("value", "")),
            )
        )
    return mentions


def _hydrate_side(node_id: str, entity_dicts: dict, provenance_rows: list) -> CandidateSide:
    """Hydrate one CandidateSide for a node id (caption/schema/aliases/props/mentions)."""
    proxy_dict = entity_dicts.get(node_id, {"id": node_id, "schema": "", "properties": {}})
    caption = _proxy_name(proxy_dict)
    return CandidateSide(
        id=node_id,
        caption=caption,
        schema=_proxy_schema(proxy_dict),
        aliases=_proxy_aliases(proxy_dict, caption),
        properties=dict(proxy_dict.get("properties") or {}),
        mentions=_mentions_for(node_id, provenance_rows),
    )


def _band_candidates(resolver, entity_dicts, provenance_rows, config):
    """Drain get_candidates() and hydrate the review-band (NO_JUDGEMENT) pairs.

    get_candidates() yields (target_id, source_id, score) for NO_JUDGEMENT pairs,
    score-DESC. The review band keeps score is not None and
    review_floor <= score < auto_threshold. A None score is treated as not-in-band.
    """
    candidates = []
    for target_id, source_id, score in resolver.get_candidates():
        if score is None:
            continue
        if not (config.review_floor <= score < config.auto_threshold):
            continue
        left = _hydrate_side(target_id, entity_dicts, provenance_rows)
        right = _hydrate_side(source_id, entity_dicts, provenance_rows)
        candidates.append(Candidate(left=left, right=right, score=score))
    return candidates


def _candidate_snapshot_args(investigation_id, config):
    """The build_candidate_snapshot kwargs resolve + apply share (so the hash matches)."""
    return {
        "investigation_id": investigation_id,
        "algorithm": config.algorithm,
        "thresholds": {
            "auto_threshold": config.auto_threshold,
            "review_floor": config.review_floor,
        },
    }


# ---------------------------------------------------------------------------
# run.json (re-load the same inputs across process invocations)
# ---------------------------------------------------------------------------

def _write_run_json(scratch_dir, entities_paths, investigation_id, config) -> None:
    run = {
        "entities_paths": [str(p) for p in entities_paths],
        "investigation_id": investigation_id,
        "config": {
            "algorithm": config.algorithm,
            "auto_threshold": config.auto_threshold,
            "review_floor": config.review_floor,
        },
    }
    path = pathlib.Path(scratch_dir) / _RUN_JSON
    path.write_text(json.dumps(run), encoding="utf-8")


def _read_run_json(scratch_dir) -> dict:
    path = pathlib.Path(scratch_dir) / _RUN_JSON
    return json.loads(path.read_text(encoding="utf-8"))


def _config_from_run(run: dict, fallback: ResolutionConfig) -> ResolutionConfig:
    """Reconstruct the resolve-time ResolutionConfig from run.json.

    run.json is the source of truth for the config resolve() actually used, so
    the apply/build recompute MUST use it (not the caller's param) -- a config
    drift between resolve and apply would otherwise pick a different candidate
    band -> a different packet_hash -> a misleading "resolver moved" abort. Older
    artifacts without a "config" key fall back to the passed config.
    """
    cfg = run.get("config")
    if not cfg:
        return fallback
    return ResolutionConfig(
        algorithm=cfg.get("algorithm", fallback.algorithm),
        auto_threshold=cfg.get("auto_threshold", fallback.auto_threshold),
        review_floor=cfg.get("review_floor", fallback.review_floor),
    )


# ---------------------------------------------------------------------------
# Investigation id (the bundle dataset_namespace, from the sibling manifest)
# ---------------------------------------------------------------------------

def _investigation_id_for(entities_paths) -> str:
    """The dataset_namespace from the first bundle's sibling manifest.

    entity_ftmize writes <name>.manifest.json carrying dataset_namespace (the
    corpus/run identity). Falls back to the entities file's base name if the
    manifest is absent.
    """
    first = pathlib.Path(entities_paths[0])
    base = _base_name_for(first)
    manifest_path = first.with_name(base + ".manifest.json")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ns = manifest.get("dataset_namespace")
        if ns:
            return ns
    return base


# ---------------------------------------------------------------------------
# resolver_db hash (snapshot metadata; does NOT affect packet_hash)
# ---------------------------------------------------------------------------

def _resolver_db_hash(scratch_dir) -> str:
    """sha256 of the resolver.db bytes, prefixed "sha256:" (metadata only)."""
    db_path = pathlib.Path(scratch_dir) / _RESOLVER_DB
    if not db_path.exists():
        return "sha256:"
    digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
    return "sha256:" + digest


# ---------------------------------------------------------------------------
# Auto-merge log (design D7)
# ---------------------------------------------------------------------------

def _pair_ids(pair) -> list:
    """Best-effort extraction of the two endpoint ids from a resolver Pair."""
    try:
        return [str(x) for x in pair]
    except TypeError:
        return [str(pair)]


def _write_auto_merge_log(scratch_dir, resolver, entity_dicts, config) -> str:
    """Write scratch/auto_merge_log.jsonl from the POSITIVE magpie-auto edges.

    Right after xref (before any human decision) every POSITIVE edge tagged
    user="magpie-auto" is an auto-merge. We group the members of each such edge by
    get_canonical and emit one row per auto-merged cluster:
      {canonical, members:[ids], names:[...], score, algorithm, auto_threshold}.
    For the fixture this log MAY be empty (two identical names can land in the
    review band under logic-v2, not auto-merge); an empty file is handled
    gracefully by every reader.
    """
    clusters: dict = {}
    for pair, edge in list(getattr(resolver, "edges", {}).items()):
        if getattr(edge, "judgement", None) != Judgement.POSITIVE:
            continue
        if getattr(edge, "user", None) != _AUTO_USER:
            continue
        members = _pair_ids(pair)
        score = getattr(edge, "score", None)
        canon = resolver.get_canonical(members[0])
        bucket = clusters.setdefault(
            canon, {"members": set(), "score": score}
        )
        bucket["members"].update(members)
        # Keep the highest score seen for the cluster (None-safe).
        if score is not None and (
            bucket["score"] is None or score > bucket["score"]
        ):
            bucket["score"] = score

    log_path = pathlib.Path(scratch_dir) / _AUTO_MERGE_LOG
    with log_path.open("w", encoding="utf-8") as fh:
        for canon in sorted(clusters):
            members = sorted(clusters[canon]["members"])
            names = [
                _proxy_name(entity_dicts.get(m, {})) for m in members
            ]
            row = {
                "canonical": canon,
                "members": members,
                "names": [n for n in names if n],
                "score": clusters[canon]["score"],
                "algorithm": config.algorithm,
                "auto_threshold": config.auto_threshold,
            }
            fh.write(json.dumps(row))
            fh.write("\n")
    return str(log_path)


def _write_empty_auto_merge_log(scratch_dir) -> str:
    """Best-effort empty auto-merge log (used when the real write fails).

    The auto-merge log is a DERIVATIVE artifact; if building it raises we still
    publish an empty (but well-formed) file so downstream readers see a valid,
    empty JSONL rather than a missing path.
    """
    log_path = pathlib.Path(scratch_dir) / _AUTO_MERGE_LOG
    log_path.write_text("", encoding="utf-8")
    return str(log_path)


# ---------------------------------------------------------------------------
# resolve (step 1 of the flow; design D5/D6/D7)
# ---------------------------------------------------------------------------

def resolve(
    entities_paths,
    scratch_dir,
    config: ResolutionConfig,
    *,
    generated_at: str = "",
) -> ResolveResult:
    """Run entity resolution over the bundle(s) and persist the review artifacts.

    Sets NOMENKLATURA_DB_URL under scratch_dir, runs xref(LogicV2) over the
    combined store, writes the auto-merge log, drains the review band into a
    candidate snapshot (its hash IS packet_hash), and records run.json so
    apply_verdicts + build_resolved_snapshot reload the SAME inputs. generated_at
    is INJECTED (never a clock read) and does NOT affect packet_hash.
    """
    entities_paths = [str(p) for p in entities_paths]
    scratch = pathlib.Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)

    investigation_id = _investigation_id_for(entities_paths)
    entity_dicts = _read_entity_lines(entities_paths)
    provenance_rows = _read_provenance(entities_paths)

    resolver = _open_resolver(scratch)
    resolver.begin()
    try:
        store = _load_store(entities_paths, resolver, scratch_dir=scratch)
        index_dir = scratch / _XREF_INDEX
        index_dir.mkdir(parents=True, exist_ok=True)
        xref(
            resolver,
            store,
            index_dir,
            algorithm=LogicV2,
            auto_threshold=config.auto_threshold,
            user=_AUTO_USER,
        )
        # Auto-merge log: read the in-memory POSITIVE magpie-auto edges BEFORE
        # commit (the edge map is populated during the transaction). The log is a
        # DERIVATIVE artifact -- a failure here (odd edge object, disk issue) must
        # NOT abort resolve, which still owes its resolver state + candidate
        # snapshot. On failure, publish a best-effort empty log and continue.
        try:
            auto_merge_log_path = _write_auto_merge_log(
                scratch, resolver, entity_dicts, config
            )
        except Exception:
            auto_merge_log_path = _write_empty_auto_merge_log(scratch)
        resolver.commit()

        # Review band: drain the committed NO_JUDGEMENT candidates.
        candidates = _band_candidates(
            resolver, entity_dicts, provenance_rows, config
        )
    finally:
        resolver.close()

    snapshot, packet_hash = build_candidate_snapshot(
        candidates,
        resolver_db_hash=_resolver_db_hash(scratch),
        generated_at=generated_at,
        **_candidate_snapshot_args(investigation_id, config),
    )
    candidate_snapshot_path = scratch / _CANDIDATE_SNAPSHOT
    candidate_snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    _write_run_json(scratch, entities_paths, investigation_id, config)

    return ResolveResult(
        candidate_snapshot_path=str(candidate_snapshot_path),
        packet_hash=packet_hash,
        auto_merge_log_path=auto_merge_log_path,
    )


# ---------------------------------------------------------------------------
# apply_verdicts (FAIL-CLOSED; design D6)
# ---------------------------------------------------------------------------

def apply_verdicts(verdict_json_path, scratch_dir, config: ResolutionConfig) -> ApplyResult:
    """Apply an exported verdict file FAIL-CLOSED (design D6).

    Recomputes the live candidate-snapshot packet_hash EXACTLY as resolve did
    (same hydration + build_candidate_snapshot args, read from run.json); if it
    differs from the verdict file's packet_hash, applies NOTHING and returns an
    aborted_reason. Otherwise, per verdict, re-checks the pair is STILL
    NO_JUDGEMENT before deciding (merge -> POSITIVE, distinct -> NEGATIVE,
    unsure -> skip); a drifted pair is skipped + reported.
    """
    scratch = pathlib.Path(scratch_dir)
    run = _read_run_json(scratch)
    entities_paths = run["entities_paths"]
    investigation_id = run["investigation_id"]
    # run.json is the source of truth for the resolve-time config; the recompute
    # MUST match resolve regardless of the caller's `config` param.
    recompute_config = _config_from_run(run, config)

    verdict_text = pathlib.Path(verdict_json_path).read_text(encoding="utf-8")
    verdict_packet_hash, verdicts = parse_verdicts(verdict_text)

    entity_dicts = _read_entity_lines(entities_paths)
    provenance_rows = _read_provenance(entities_paths)

    resolver = _open_resolver(scratch)
    resolver.begin()
    try:
        _load_store(entities_paths, resolver, scratch_dir=scratch)

        # Recompute the live packet_hash (resolver_db_hash / generated_at do NOT
        # affect the hash, so any value is fine here).
        live_candidates = _band_candidates(
            resolver, entity_dicts, provenance_rows, recompute_config
        )
        _snapshot, live_packet_hash = build_candidate_snapshot(
            live_candidates,
            resolver_db_hash="sha256:",
            generated_at="",
            **_candidate_snapshot_args(investigation_id, recompute_config),
        )

        if live_packet_hash != verdict_packet_hash:
            return ApplyResult(
                applied=0,
                skipped=0,
                aborted_reason=(
                    "resolver moved since the packet was generated -- "
                    "regenerate the review packet"
                ),
            )

        applied = 0
        skipped = 0
        for verdict in verdicts:
            left = verdict.left_id
            right = verdict.right_id
            if verdict.verdict == "unsure":
                skipped += 1
                continue
            # Re-check the pair is STILL a live NO_JUDGEMENT candidate.
            if resolver.get_judgement(left, right) != Judgement.NO_JUDGEMENT:
                skipped += 1
                continue
            if verdict.verdict == "merge":
                resolver.decide(
                    left, right, Judgement.POSITIVE, user=_REVIEW_USER
                )
            else:  # "distinct"
                resolver.decide(
                    left, right, Judgement.NEGATIVE, user=_REVIEW_USER
                )
            applied += 1
        resolver.commit()
    finally:
        resolver.close()

    return ApplyResult(applied=applied, skipped=skipped, aborted_reason=None)


# ---------------------------------------------------------------------------
# build_resolved_snapshot (design D2/D3/D4)
# ---------------------------------------------------------------------------

def _representative_caption(member_ids, entity_dicts) -> str:
    """A stable representative name for a cluster (first non-empty by sorted id)."""
    for member in sorted(member_ids):
        name = _proxy_name(entity_dicts.get(member, {}))
        if name:
            return name
    return ""


def _merge_properties(member_ids, entity_dicts) -> dict:
    """Union member properties into one dict[str, list[str]] (de-duplicated, ordered)."""
    merged: dict = {}
    for member in sorted(member_ids):
        props = (entity_dicts.get(member, {}).get("properties") or {})
        for key in props:
            slot = merged.setdefault(key, [])
            for value in props[key]:
                if value not in slot:
                    slot.append(value)
    return merged


def _provenance_refs_for(target_ids, provenance_rows) -> list:
    """The statement_ids of provenance rows whose target_id is in target_ids."""
    wanted = set(target_ids)
    refs = []
    for row in provenance_rows:
        if row.get("target_id") in wanted:
            ref = row.get("statement_id")
            if ref is not None and ref not in refs:
                refs.append(ref)
    return refs


def build_resolved_snapshot(
    scratch_dir,
    investigation_id: str,
    config: ResolutionConfig,
    *,
    generated_at: str = "",
) -> dict:
    """Build the portable resolved snapshot (design D2/D3/D4).

    Reopens the resolver + reloads entities_paths from run.json (NOT an in-memory
    arg). Clusters every node by resolver.get_canonical, derives the STABLE Magpie
    canonical_id per cluster, remaps every member edge's endpoints to their
    cluster canonical_ids, then COALESCES edges by edge_id (one ResolvedEdge per
    canonical edge with unioned provenance + merged properties). generated_at is
    INJECTED. assert_snapshot_consumable is called as a self-check.
    """
    scratch = pathlib.Path(scratch_dir)
    run = _read_run_json(scratch)
    entities_paths = run["entities_paths"]
    # run.json is the source of truth for BOTH the resolve-time IDENTITY and
    # config. The snapshot's investigation_id MUST be the one resolve persisted,
    # never a caller-supplied relabel -- mislabeling would write this run's
    # resolved membership into the WRONG Neo4j investigation scope (the scoped_id
    # is investigation_id + ":" + canonical_id). A passed value that disagrees is
    # a wrong-scope call and fails fast.
    run_investigation_id = run.get("investigation_id")
    if (
        investigation_id
        and run_investigation_id
        and investigation_id != run_investigation_id
    ):
        raise ValueError(
            "build_resolved_snapshot investigation_id %r does not match run.json's"
            " %r -- the snapshot is scoped to the resolve-time investigation; do"
            " not relabel it" % (investigation_id, run_investigation_id)
        )
    investigation_id = run_investigation_id or investigation_id
    # Snapshot metadata (algorithm/thresholds) MUST reflect what resolve used.
    snapshot_config = _config_from_run(run, config)

    entity_dicts = _read_entity_lines(entities_paths)
    provenance_rows = _read_provenance(entities_paths)

    # Split node proxies from edge proxies via followthemoney (schema.edge flag).
    node_ids = []
    edge_records = []  # (schema, head_id, tail_id, role, edge_member_id)
    for proxy_dict in entity_dicts.values():
        proxy = model.get_proxy(proxy_dict)
        if proxy.schema.edge:
            head_vals = list(proxy.get(proxy.schema.edge_source))
            tail_vals = list(proxy.get(proxy.schema.edge_target))
            role_vals = list(proxy.get("role", quiet=True))
            edge_records.append(
                {
                    "schema": proxy.schema.name,
                    "head_id": head_vals[0] if head_vals else None,
                    "tail_id": tail_vals[0] if tail_vals else None,
                    "role": role_vals[0] if role_vals else None,
                    "member_id": proxy.id,
                }
            )
        else:
            node_ids.append(proxy.id)

    resolver = _open_resolver(scratch)
    resolver.begin()
    try:
        # CLUSTER: group node ids by resolver.get_canonical.
        groups: dict = {}
        for node_id in node_ids:
            key = resolver.get_canonical(node_id)
            groups.setdefault(key, []).append(node_id)
    finally:
        resolver.close()

    # Per cluster -> ResolvedEntity; build member_id -> canonical_id map for edges.
    entities = []
    member_to_canonical: dict = {}
    for group_key in sorted(groups):
        members = groups[group_key]
        cid = canonical_id(members)
        for member in members:
            member_to_canonical[member] = cid
        # resolver_id is the NK- canonical only for a real (multi-member) merge.
        resolver_id = group_key if len(members) > 1 else None
        caption = _representative_caption(members, entity_dicts)
        member_names = []
        for member in sorted(members):
            nm = _proxy_name(entity_dicts.get(member, {}))
            if nm and nm != caption and nm not in member_names:
                member_names.append(nm)
        schema = ""
        for member in sorted(members):
            schema = _proxy_schema(entity_dicts.get(member, {}))
            if schema:
                break
        entities.append(
            entity_resolved_snapshot.ResolvedEntity(
                canonical_id=cid,
                schema=schema,
                caption=caption,
                aliases=member_names,
                member_ids=sorted(members),
                properties=_merge_properties(members, entity_dicts),
                resolver_id=resolver_id,
                provenance_refs=_provenance_refs_for(members, provenance_rows),
            )
        )

    # EDGES: remap endpoints member->canonical, then COALESCE by edge_id.
    coalesced: dict = {}
    for rec in edge_records:
        head_member = rec["head_id"]
        tail_member = rec["tail_id"]
        head_canonical = member_to_canonical.get(head_member)
        tail_canonical = member_to_canonical.get(tail_member)
        if head_canonical is None or tail_canonical is None:
            # Endpoint outside the resolved node set -- skip (defensive).
            continue
        role = rec["role"]
        eid = edge_id(rec["schema"], head_canonical, tail_canonical, role)
        slot = coalesced.get(eid)
        edge_refs = _provenance_refs_for([rec["member_id"]], provenance_rows)
        edge_props = _merge_properties([rec["member_id"]], entity_dicts)
        if slot is None:
            coalesced[eid] = entity_resolved_snapshot.ResolvedEdge(
                edge_id=eid,
                schema=rec["schema"],
                head_canonical=head_canonical,
                tail_canonical=tail_canonical,
                role=role,
                properties=edge_props,
                provenance_refs=list(edge_refs),
            )
        else:
            for ref in edge_refs:
                if ref not in slot.provenance_refs:
                    slot.provenance_refs.append(ref)
            for key in edge_props:
                existing = slot.properties.setdefault(key, [])
                for value in edge_props[key]:
                    if value not in existing:
                        existing.append(value)
    edges = [coalesced[eid] for eid in sorted(coalesced)]

    # provenance[] = the flat provenance rows in the snapshot's portable shape.
    provenance = []
    for row in provenance_rows:
        provenance.append(
            {
                "ref_id": row.get("statement_id"),
                "doc_id": row.get("doc_id"),
                "page": row.get("page"),
                "char_start": row.get("char_start"),
                "char_end": row.get("char_end"),
                "model": row.get("model"),
                "confidence": row.get("confidence"),
            }
        )

    snapshot = entity_resolved_snapshot.build_snapshot(
        entities,
        edges,
        provenance,
        investigation_id=investigation_id,
        algorithm=snapshot_config.algorithm,
        thresholds={
            "auto_threshold": snapshot_config.auto_threshold,
            "review_floor": snapshot_config.review_floor,
        },
        generated_at=generated_at,
    )
    entity_resolved_snapshot.assert_snapshot_consumable(snapshot)
    return snapshot
