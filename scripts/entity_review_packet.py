"""Phase 13a HITL review-packet generator + verdict handback (pure stdlib core).

ASCII only. Produces the self-contained HTML review packet (design D6) and MUST
match the signed-off mockup docs/plans/2026-06-07-phase13a-review-packet-mockup
.html: the <style> block (light :root vars + the prefers-color-scheme dark vars +
every component class), the side-by-side candidate cards with hydrated source
snippets, the expandable "More evidence" panel (additional mentions + wider
context + disambiguating properties), the merge/distinct/unsure controls, the
fixed action bar, the export <dialog>, and the verdict-JSON export <script>.

Pure: stdlib only (hashlib / html / json / dataclasses / typing). It imports NO
nomenklatura / neo4j / followthemoney, so the renderer is Windows-golden-testable
with a FAKE snippet_resolver. The only neighbor import is the Task-1 policy core
(itself pure), for the Candidate / Verdict dataclasses + VALID_VERDICTS.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html
import json
from typing import Callable

from scripts.entity_resolution_policy import (
    Candidate,
    Verdict,
    VALID_VERDICTS,
)


# ---------------------------------------------------------------------------
# Public type alias: the injected source-text resolver
# ---------------------------------------------------------------------------

# snippet_resolver(doc_id, page, char_start, char_end, *, context_chars=0) -> str
# Returns the SOURCE TEXT of a window around one mention. context_chars WIDENS the
# window (0 = the tight default for the collapsed top mention; a larger value for
# the expanded more-evidence view). INJECTED so render_html is testable with a
# fake; snippets are LOCAL raw source text (never persisted, never published).
SnippetResolver = Callable[..., str]


# Window-width policy: the collapsed top mention uses the tight (default) window;
# the expanded more-evidence mentions request a wider window.
_WIDE_CONTEXT_CHARS = 240


# ---------------------------------------------------------------------------
# Canonical JSON (the hashing + embedding primitive)
# ---------------------------------------------------------------------------

def canonical_json(obj) -> str:
    """Deterministic JSON: sorted keys, tight separators, ASCII-escaped.

    The single canonicalizer reused for packet_hash and the embedded export hash,
    so a recompute (Task 4) byte-matches the original.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------
# Candidate snapshot + packet hash
# ---------------------------------------------------------------------------

def build_candidate_snapshot(
    candidates: list[Candidate],
    *,
    investigation_id: str,
    algorithm: str,
    thresholds: dict,
    resolver_db_hash: str,
    generated_at: str,
) -> tuple[dict, str]:
    """Build the candidate snapshot + its packet_hash.

    Returns (candidate_snapshot, packet_hash) where:

        candidate_snapshot = {
          "metadata": {investigation_id, algorithm, thresholds, resolver_db_hash,
                       generated_at, packet_version: "1.0"},
          "candidates": [ dataclasses.asdict(candidate), ... ]   # sorted
        }

    The candidates list is sorted by (left.id, right.id) for a DETERMINISTIC
    order independent of input order.

    CRITICAL (the load-bearing fail-closed property): packet_hash is sha256 over
    the CANDIDATES payload ONLY -- NOT over metadata. generated_at and
    resolver_db_hash are volatile (wall-clock / resolver-DB churn), so excluding
    them lets Task 4's apply_verdicts RECOMPUTE the identical packet_hash from the
    live resolver (which reproduces the same candidate set) WITHOUT knowing the
    original generation time. The candidates' display fields
    (caption/aliases/properties/mentions) ARE in the hash on purpose: any drift in
    the reviewed candidate set moves the hash and trips the fail-closed guard.
    """
    serialized = [dataclasses.asdict(c) for c in candidates]
    serialized.sort(key=lambda c: (c["left"]["id"], c["right"]["id"]))

    snapshot = {
        "metadata": {
            "investigation_id": investigation_id,
            "algorithm": algorithm,
            "thresholds": thresholds,
            "resolver_db_hash": resolver_db_hash,
            "generated_at": generated_at,
            "packet_version": "1.0",
        },
        "candidates": serialized,
    }
    packet_hash = _packet_hash(serialized)
    return snapshot, packet_hash


def _packet_hash(candidates_payload: list) -> str:
    """sha256 of the canonical-JSON candidates payload (metadata excluded)."""
    return hashlib.sha256(
        canonical_json(candidates_payload).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# HTML render (matches the signed-off mockup)
# ---------------------------------------------------------------------------

def render_html(candidate_snapshot: dict, snippet_resolver: SnippetResolver) -> str:
    """Render the self-contained HTML review packet (matches the mockup, D6).

    ONE offline HTML document: no external CSS/JS/fonts/images, no http(s) URLs,
    no external src=. The <style> + <script> are reproduced from the signed-off
    mockup; only the script's demo PACKET_HASH / INVESTIGATION constants are
    replaced with the real values. The packet_hash is RECOMPUTED here from
    candidate_snapshot["candidates"] (so the embedded hash always matches what
    build_candidate_snapshot produced for the same candidates).

    Every user-derived string (captions, aliases, property keys/values, doc_ids,
    snippet text, ids in data-* attributes) is html.escape'd. Snippet marking:
    the escaped mention text is wrapped in <mark>...</mark> at its first
    occurrence inside the escaped snippet (no mark if empty / not found).

    Deterministic: the same (snapshot, resolver) yields identical HTML.
    """
    metadata = candidate_snapshot["metadata"]
    candidates = candidate_snapshot["candidates"]
    investigation_id = metadata.get("investigation_id", "")
    packet_hash = _packet_hash(candidates)

    total = len(candidates)
    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>Magpie - Entity Resolution Review</title>")
    parts.append(_STYLE_BLOCK)
    parts.append("</head>")
    parts.append("<body>")
    parts.append('<div class="wrap">')
    parts.append(_render_header())
    parts.append(_render_meta(metadata, total))
    parts.append(_render_instructions())
    for index, candidate in enumerate(candidates, start=1):
        parts.append(_render_pair(candidate, index, total, snippet_resolver))
    parts.append("</div>")  # .wrap
    parts.append(_render_actionbar(total))
    parts.append(_render_dialog())
    parts.append(_render_script(investigation_id, packet_hash))
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"


def _render_header() -> str:
    return (
        "<header>\n"
        "    <h1>Entity Resolution Review</h1>\n"
        '    <p class="lede">Decide which entities across your documents are the '
        "same real-world person or organization.</p>\n"
        "</header>"
    )


def _render_meta(metadata: dict, total: int) -> str:
    inv = html.escape(str(metadata.get("investigation_id", "")))
    algorithm = html.escape(str(metadata.get("algorithm", "")))
    generated_at = html.escape(str(metadata.get("generated_at", "")))
    band = _format_review_band(metadata.get("thresholds") or {})

    rows = []
    rows.append("<div class=\"meta\">")
    rows.append('  <span><b>Investigation</b> <code>' + inv + "</code></span>")
    rows.append("  <span><b>Algorithm</b> " + algorithm + "</span>")
    rows.append("  <span><b>Review band</b> " + band + "</span>")
    # The auto-merged chip is shown ONLY when the snapshot carries the count.
    auto_count = metadata.get("auto_merged_count")
    if auto_count is not None:
        auto_thr = _format_threshold(
            (metadata.get("thresholds") or {}).get("auto_threshold")
        )
        rows.append(
            "  <span><b>Auto-merged (&ge;"
            + auto_thr
            + ")</b> "
            + html.escape(str(auto_count))
            + " (logged, reversible)</span>"
        )
    rows.append("  <span><b>Generated</b> " + generated_at + "</span>")
    rows.append(
        "  <span><b>To review</b> " + html.escape(str(total)) + " pairs</span>"
    )
    rows.append("</div>")
    return "\n".join(rows)


def _render_instructions() -> str:
    return (
        '<p class="instructions">For each pair the evidence is shown <em>before</em> '
        "the suggestion. Read the source snippets &mdash; and open <b>More evidence</b> "
        "for additional mentions, wider context, and identifying details &mdash; then "
        "choose. Nothing changes your graph until you export and apply these verdicts.</p>"
    )


def _render_pair(
    candidate: dict,
    index: int,
    total: int,
    snippet_resolver: SnippetResolver,
) -> str:
    left = candidate["left"]
    right = candidate["right"]
    score = candidate["score"]

    left_id_attr = html.escape(str(left.get("id", "")), quote=True)
    right_id_attr = html.escape(str(right.get("id", "")), quote=True)
    score_cls = _score_class(score)
    score_txt = _format_score(score)
    same_q = _same_question(left.get("schema", ""), right.get("schema", ""))

    out = []
    out.append(
        '<section class="pair" data-left="'
        + left_id_attr
        + '" data-right="'
        + right_id_attr
        + '">'
    )
    # pair-head
    out.append('  <div class="pair-head">')
    out.append(
        '    <span class="ix">Pair '
        + str(index)
        + " of "
        + str(total)
        + "</span>"
    )
    out.append(
        '    <span class="score '
        + score_cls
        + '"><span class="lbl">match</span> '
        + score_txt
        + "</span>"
    )
    out.append('    <span class="same-q">' + same_q + "</span>")
    out.append("  </div>")
    # cols (collapsed: top mention)
    out.append('  <div class="cols">')
    out.append(_render_top_col(left, snippet_resolver))
    out.append(_render_top_col(right, snippet_resolver))
    out.append("  </div>")
    # more-evidence
    out.append(_render_more(left, right, snippet_resolver))
    # decide
    out.append(_render_decide(index))
    out.append("</section>")
    return "\n".join(out)


def _render_top_col(side: dict, snippet_resolver: SnippetResolver) -> str:
    caption = html.escape(str(side.get("caption", "")))
    out = []
    out.append('    <div class="col">')
    out.append('      <h3 class="ename">' + caption + "</h3>")
    out.append('      <div class="chips">' + _render_chips(side) + "</div>")
    out.append('      <div class="snip-label">Mentioned in</div>')
    mentions = side.get("mentions") or []
    if mentions:
        out.append("      " + _render_snippet(mentions[0], snippet_resolver, context_chars=0))
    out.append("    </div>")
    return "\n".join(out)


def _render_chips(side: dict) -> str:
    chips = []
    schema_label = _schema_chip_label(side.get("schema", ""))
    chips.append('<span class="chip">' + html.escape(schema_label) + "</span>")
    for alias in side.get("aliases") or []:
        chips.append(
            '<span class="chip alias">alias: ' + html.escape(str(alias)) + "</span>"
        )
    return "".join(chips)


def _render_more(
    left: dict,
    right: dict,
    snippet_resolver: SnippetResolver,
) -> str:
    hint = _more_hint(left, right)
    out = []
    out.append('  <details class="more">')
    out.append(
        '    <summary>More evidence <span class="hint">' + hint + "</span></summary>"
    )
    out.append('    <div class="more-body cols">')
    out.append(_render_more_col(left, snippet_resolver))
    out.append(_render_more_col(right, snippet_resolver))
    out.append("    </div>")
    out.append("  </details>")
    return "\n".join(out)


def _render_more_col(side: dict, snippet_resolver: SnippetResolver) -> str:
    out = []
    out.append('      <div class="col">')
    out.append('        <div class="snip-label">Identifying details</div>')
    out.append('        <div class="kv">' + _render_kv(side) + "</div>")
    extra_mentions = (side.get("mentions") or [])[1:]
    for mention in extra_mentions:
        out.append(
            "        "
            + _render_snippet(
                mention, snippet_resolver, context_chars=_WIDE_CONTEXT_CHARS
            )
        )
    out.append("      </div>")
    return "\n".join(out)


def _render_kv(side: dict) -> str:
    pills = []
    properties = side.get("properties") or {}
    for key in properties:
        for value in properties[key]:
            pills.append(
                '<span class="pill"><b>'
                + html.escape(str(key))
                + "</b>"
                + html.escape(str(value))
                + "</span>"
            )
    return "".join(pills)


def _render_decide(index: int) -> str:
    label = html.escape("Decision for pair " + str(index), quote=True)
    return (
        '  <div class="decide">\n'
        '    <span class="q">Decision:</span>\n'
        '    <div class="seg" role="group" aria-label="' + label + '">\n'
        '      <button data-v="merge" aria-pressed="false">Merge</button>\n'
        '      <button data-v="distinct" aria-pressed="false">Keep distinct</button>\n'
        '      <button data-v="unsure" aria-pressed="false">Unsure</button>\n'
        "    </div>\n"
        '    <span class="decided-flag" hidden>&#10003; decided</span>\n'
        "  </div>"
    )


def _render_snippet(
    mention: dict,
    snippet_resolver: SnippetResolver,
    *,
    context_chars: int,
) -> str:
    doc_id = str(mention.get("doc_id", ""))
    page = mention.get("page", "")
    char_start = mention.get("char_start", 0)
    char_end = mention.get("char_end", 0)
    mention_text = str(mention.get("text", ""))

    if context_chars:
        raw = snippet_resolver(
            doc_id, page, char_start, char_end, context_chars=context_chars
        )
    else:
        raw = snippet_resolver(doc_id, page, char_start, char_end)

    marked = _mark_snippet(str(raw), mention_text)
    src = (
        '<span class="src">in <code>'
        + html.escape(doc_id)
        + "</code> &middot; p."
        + html.escape(str(page))
        + "</span>"
    )
    return '<div class="snip">' + marked + src + "</div>"


def _mark_snippet(snippet_text: str, mention_text: str) -> str:
    """html.escape both, then wrap the FIRST occurrence of the escaped mention in
    <mark>...</mark>. If mention is empty or not found, return the escaped snippet
    with no mark (never crash)."""
    escaped_snippet = html.escape(snippet_text)
    if not mention_text:
        return escaped_snippet
    escaped_mention = html.escape(mention_text)
    idx = escaped_snippet.find(escaped_mention)
    if idx == -1:
        return escaped_snippet
    return (
        escaped_snippet[:idx]
        + "<mark>"
        + escaped_mention
        + "</mark>"
        + escaped_snippet[idx + len(escaped_mention):]
    )


def _render_actionbar(total: int) -> str:
    return (
        '<div class="actionbar">\n'
        '  <div class="inner">\n'
        '    <span class="progress"><span class="n" id="done">0</span> of '
        '<span id="total">' + str(total) + "</span> decided</span>\n"
        '    <span class="spacer"></span>\n'
        '    <button class="btn" id="exportBtn" disabled>Export verdicts (JSON)</button>\n'
        "  </div>\n"
        "</div>"
    )


def _render_dialog() -> str:
    return (
        '<dialog id="exportDlg">\n'
        '  <div class="dlg-head">Verdicts ready to apply</div>\n'
        '  <div class="dlg-body">\n'
        "    <p>This file is saved next to the review packet. Run the skill's apply step "
        "to write these\n"
        "       decisions to the resolver. The <code>packet_hash</code> is checked on "
        "apply &ndash; if the\n"
        "       resolver moved since this packet was generated, the apply aborts and asks "
        "you to regenerate.</p>\n"
        '    <textarea id="jsonOut" readonly></textarea>\n'
        '    <p class="hint" id="copyHint"></p>\n'
        "  </div>\n"
        '  <div class="dlg-foot">\n'
        '    <button class="btn ghost" id="copyBtn">Copy</button>\n'
        '    <button class="btn ghost" id="downloadBtn">Download verdicts.json</button>\n'
        '    <button class="btn" id="closeDlg">Done</button>\n'
        "  </div>\n"
        "</dialog>"
    )


def _render_script(investigation_id: str, packet_hash: str) -> str:
    # Embed the REAL values. json.dumps yields a safe ASCII JS string literal and
    # escapes any </script> sequence as <\/script> via the slash, but to be safe
    # we additionally guard the closing-tag breakout below.
    inv_literal = _js_string(investigation_id)
    hash_literal = _js_string("sha256:" + packet_hash)
    return (
        "<script>\n"
        "(function(){\n"
        "  var PACKET_HASH = " + hash_literal + ";\n"
        "  var INVESTIGATION = " + inv_literal + ";\n"
        "  var pairs = Array.prototype.slice.call(document.querySelectorAll('.pair'));\n"
        "  var verdicts = {};\n"
        "\n"
        "  function refresh(){\n"
        "    var n = Object.keys(verdicts).length;\n"
        "    document.getElementById('done').textContent = n;\n"
        "    document.getElementById('exportBtn').disabled = (n === 0);\n"
        "  }\n"
        "\n"
        "  pairs.forEach(function(pair){\n"
        "    var left = pair.getAttribute('data-left'), right = pair.getAttribute('data-right');\n"
        "    var key = left + '|' + right;\n"
        "    var flag = pair.querySelector('.decided-flag');\n"
        "    pair.querySelectorAll('.seg button').forEach(function(btn){\n"
        "      btn.addEventListener('click', function(){\n"
        "        pair.querySelectorAll('.seg button').forEach(function(b){ b.setAttribute('aria-pressed','false'); });\n"
        "        btn.setAttribute('aria-pressed','true');\n"
        "        verdicts[key] = { left:left, right:right, verdict:btn.getAttribute('data-v') };\n"
        "        flag.hidden = false;\n"
        "        refresh();\n"
        "      });\n"
        "    });\n"
        "  });\n"
        "\n"
        "  function buildJson(){\n"
        "    return JSON.stringify({\n"
        "      investigation_id: INVESTIGATION,\n"
        "      packet_hash: PACKET_HASH,\n"
        "      verdicts: Object.keys(verdicts).map(function(k){ return verdicts[k]; })\n"
        "    }, null, 2);\n"
        "  }\n"
        "\n"
        "  var dlg = document.getElementById('exportDlg');\n"
        "  document.getElementById('exportBtn').addEventListener('click', function(){\n"
        "    document.getElementById('jsonOut').value = buildJson();\n"
        "    document.getElementById('copyHint').textContent = '';\n"
        "    if (typeof dlg.showModal === 'function') dlg.showModal(); else alert(buildJson());\n"
        "  });\n"
        "  document.getElementById('closeDlg').addEventListener('click', function(){ dlg.close(); });\n"
        "  document.getElementById('copyBtn').addEventListener('click', function(){\n"
        "    var ta = document.getElementById('jsonOut'); ta.select();\n"
        "    try{ document.execCommand('copy'); document.getElementById('copyHint').textContent='Copied to clipboard.'; }\n"
        "    catch(e){ document.getElementById('copyHint').textContent='Select the text above and copy.'; }\n"
        "  });\n"
        "  var dl = document.getElementById('downloadBtn');\n"
        "  if (dl) dl.addEventListener('click', function(){\n"
        "    var blob = new Blob([buildJson()], {type:'application/json'});\n"
        "    var url = URL.createObjectURL(blob);\n"
        "    var a = document.createElement('a');\n"
        "    a.href = url; a.download = 'verdicts.json';\n"
        "    document.body.appendChild(a); a.click(); document.body.removeChild(a);\n"
        "    URL.revokeObjectURL(url);\n"
        "  });\n"
        "  refresh();\n"
        "})();\n"
        "</script>"
    )


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def _js_string(value: str) -> str:
    """A safe, ASCII JS string literal. json.dumps(ensure_ascii) escapes quotes /
    control chars; we additionally neutralize any literal </script that could
    break out of the <script> element."""
    literal = json.dumps(value, ensure_ascii=True)
    return literal.replace("</", "<\\/")


def _score_class(score: float) -> str:
    if score >= 0.90:
        return "s-hi"
    if score >= 0.80:
        return "s-mid"
    return "s-lo"


def _format_score(score: float) -> str:
    return "{0:.2f}".format(score)


def _format_threshold(value) -> str:
    if value is None:
        return ""
    try:
        return "{0:.2f}".format(float(value))
    except (TypeError, ValueError):
        return html.escape(str(value))


def _format_review_band(thresholds: dict) -> str:
    floor = _format_threshold(thresholds.get("review_floor"))
    auto = _format_threshold(thresholds.get("auto_threshold"))
    if floor and auto:
        return floor + " &ndash; " + auto
    if floor:
        return floor + " &ndash;"
    if auto:
        return "&ndash; " + auto
    return ""


def _schema_chip_label(schema: str) -> str:
    if schema == "Person":
        return "Person"
    if schema in ("Company", "Organization"):
        return "Company"
    return schema or "Entity"


def _same_question(left_schema: str, right_schema: str) -> str:
    schema = left_schema or right_schema
    if schema == "Person":
        return "Same person?"
    if schema in ("Company", "Organization"):
        return "Same organization?"
    return "Same entity?"


def _more_hint(left: dict, right: dict) -> str:
    """A short, data-derived hint mirroring the mockup's per-pair summary."""
    extra_left = max(0, len(left.get("mentions") or []) - 1)
    extra_right = max(0, len(right.get("mentions") or []) - 1)
    extra = max(extra_left, extra_right)
    has_props = bool(left.get("properties")) or bool(right.get("properties"))
    bits = []
    if extra == 1:
        bits.append("1 more mention each")
    elif extra > 1:
        bits.append(str(extra) + " more mentions each")
    if has_props:
        bits.append("details")
    if not bits:
        return "(more context)"
    return "(" + " + ".join(bits) + ")"


# ---------------------------------------------------------------------------
# Verdict handback (parse the exported JSON)
# ---------------------------------------------------------------------------

def parse_verdicts(verdict_json_text: str) -> tuple[str, list[Verdict]]:
    """Parse an exported verdict JSON into (packet_hash, [Verdict, ...]).

    Expected shape (the export the packet's <script> produces):

        {"investigation_id": <str>, "packet_hash": <str>,
         "verdicts": [{"left": <id>, "right": <id>, "verdict": <v>}, ...]}

    The JSON keys are "left"/"right"; they map to Verdict.left_id / right_id.

    Raises ValueError (naming the problem) on: malformed JSON; a non-object
    top-level; missing/empty packet_hash; "verdicts" missing or not a list; a
    verdict row that is not an object or is missing left/right/verdict; a bad
    verdict value (delegated to Verdict.__post_init__ but pre-validated here for a
    clearer message).
    """
    try:
        data = json.loads(verdict_json_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("verdict JSON is not valid JSON: " + str(exc))

    if not isinstance(data, dict):
        raise ValueError("verdict JSON must be a JSON object at the top level")

    packet_hash = data.get("packet_hash")
    if not isinstance(packet_hash, str) or not packet_hash:
        raise ValueError("verdict JSON is missing a non-empty 'packet_hash'")

    if "verdicts" not in data:
        raise ValueError("verdict JSON is missing 'verdicts'")
    rows = data["verdicts"]
    if not isinstance(rows, list):
        raise ValueError("verdict JSON 'verdicts' must be a list")

    verdicts: list[Verdict] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(
                "verdict row " + str(i) + " must be a JSON object"
            )
        left = row.get("left")
        right = row.get("right")
        value = row.get("verdict")
        if left is None or right is None or value is None:
            raise ValueError(
                "verdict row "
                + str(i)
                + " is missing one of 'left'/'right'/'verdict'"
            )
        if value not in VALID_VERDICTS:
            raise ValueError(
                "verdict row "
                + str(i)
                + " has an invalid verdict value: "
                + repr(value)
            )
        verdicts.append(
            Verdict(left_id=str(left), right_id=str(right), verdict=str(value))
        )

    return packet_hash, verdicts


# ---------------------------------------------------------------------------
# The <style> block -- reproduced VERBATIM from the signed-off mockup
# (docs/plans/2026-06-07-phase13a-review-packet-mockup.html). ASCII-only;
# the mockup itself is ASCII. Keep byte-for-byte in sync with the mockup.
# ---------------------------------------------------------------------------

_STYLE_BLOCK = """<style>
  :root{
    --bg:#f6f7f9; --card:#ffffff; --ink:#1c2024; --muted:#6b7280; --line:#e5e7eb;
    --accent:#0f766e; --accent-ink:#0b5750; --panel:#fbfcfd;
    --hi:#15803d; --hi-bg:#dcfce7; --mid:#b45309; --mid-bg:#fef3c7; --lo:#475569; --lo-bg:#e2e8f0;
    --merge:#15803d; --merge-bg:#dcfce7; --distinct:#b91c1c; --distinct-bg:#fee2e2;
    --unsure:#b45309; --unsure-bg:#fef3c7; --mark:#fde68a; --mark-ink:#7c5800;
    --snip-bg:#fafafa; --chip-bg:#eef2ff; --chip-ink:#3730a3;
    --chip-alias-bg:#f1f5f9; --chip-alias-ink:#475569; --code-bg:#f1f5f9;
    --kv-bg:#f8fafc; --shadow:0 1px 2px rgba(16,24,40,.04); --btn-ink:#ffffff;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#0e1014; --card:#171a21; --ink:#e6e8ec; --muted:#9aa3b2; --line:#2a2f3a;
      --accent:#2dd4bf; --accent-ink:#5eead4; --panel:#13161d;
      --hi:#4ade80; --hi-bg:#0f2e1c; --mid:#fbbf24; --mid-bg:#3a2c0a; --lo:#94a3b8; --lo-bg:#1e2530;
      --merge:#4ade80; --merge-bg:#0f2e1c; --distinct:#f87171; --distinct-bg:#3a1414;
      --unsure:#fbbf24; --unsure-bg:#3a2c0a; --mark:#9a7a16; --mark-ink:#fde68a;
      --snip-bg:#11141a; --chip-bg:#1e2740; --chip-ink:#c7d2fe;
      --chip-alias-bg:#1e2530; --chip-alias-ink:#cbd5e1; --code-bg:#0f1218;
      --kv-bg:#10131a; --shadow:0 1px 2px rgba(0,0,0,.3); --btn-ink:#06231f;
    }
  }
  *{box-sizing:border-box}
  body{
    margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    padding-bottom:84px;
  }
  .wrap{max-width:980px; margin:0 auto; padding:28px 20px 40px}
  header h1{font-size:22px; margin:0 0 4px; letter-spacing:-0.01em}
  header p.lede{margin:0 0 16px; color:var(--muted)}
  .meta{
    display:flex; flex-wrap:wrap; gap:8px 18px; padding:12px 16px; margin-bottom:18px;
    background:var(--card); border:1px solid var(--line); border-radius:10px; font-size:13px;
  }
  .meta b{color:var(--ink)} .meta span{color:var(--muted)}
  .meta code{background:var(--code-bg); padding:1px 5px; border-radius:4px; font-size:12px}
  .instructions{font-size:14px; color:var(--muted); margin:0 0 20px}
  .pair{
    background:var(--card); border:1px solid var(--line); border-radius:12px;
    margin-bottom:18px; overflow:hidden; box-shadow:var(--shadow);
  }
  .pair-head{
    position:relative; display:flex; align-items:center; justify-content:center; gap:14px;
    padding:12px 16px; border-bottom:1px solid var(--line); background:var(--panel);
  }
  .pair-head .ix{position:absolute; left:16px; color:var(--muted); font-size:12px; font-weight:600}
  .score{font-weight:700; font-size:13px; padding:4px 11px; border-radius:999px; display:inline-flex; align-items:center; gap:6px}
  .score .lbl{font-weight:600; opacity:.8}
  .score.s-hi{color:var(--hi); background:var(--hi-bg)}
  .score.s-mid{color:var(--mid); background:var(--mid-bg)}
  .score.s-lo{color:var(--lo); background:var(--lo-bg)}
  .same-q{font-size:13px; color:var(--muted)}
  .cols{display:grid; grid-template-columns:1fr 1fr; gap:0}
  .col{padding:16px 18px}
  .col:first-child{border-right:1px solid var(--line)}
  .ename{font-size:17px; font-weight:650; margin:0 0 6px; letter-spacing:-0.01em}
  .chips{display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px}
  .chip{font-size:11px; font-weight:600; padding:2px 8px; border-radius:6px; background:var(--chip-bg); color:var(--chip-ink)}
  .chip.alias{background:var(--chip-alias-bg); color:var(--chip-alias-ink); font-weight:500}
  .snip-label{font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:12px 0 5px; font-weight:600}
  .snip{font-size:13px; line-height:1.55; color:var(--ink); background:var(--snip-bg); border-left:3px solid var(--line); padding:8px 11px; border-radius:0 6px 6px 0; margin:0 0 8px}
  .snip .src{display:block; margin-top:5px; font-size:11.5px; color:var(--muted)}
  .snip .src code{background:var(--code-bg); padding:1px 5px; border-radius:4px}
  mark{background:var(--mark); padding:0 2px; border-radius:3px; font-weight:600; color:var(--mark-ink)}
  /* expandable more-evidence panel (native <details>, no JS, works offline) */
  details.more{border-top:1px dashed var(--line); background:var(--panel)}
  details.more>summary{
    list-style:none; cursor:pointer; user-select:none; padding:10px 18px;
    font-size:13px; font-weight:600; color:var(--accent); display:flex; align-items:center; gap:7px;
  }
  details.more>summary::-webkit-details-marker{display:none}
  details.more>summary::before{content:"\\25B8"; font-size:11px; transition:transform .12s}
  details.more[open]>summary::before{transform:rotate(90deg)}
  details.more>summary:focus-visible{outline:2px solid var(--accent); outline-offset:-2px}
  details.more>summary .hint{font-weight:500; color:var(--muted); font-size:12px}
  .more-body{border-top:1px solid var(--line)}
  .kv{display:flex; flex-wrap:wrap; gap:5px; margin:2px 0 10px}
  .kv .pill{font-size:12px; background:var(--kv-bg); border:1px solid var(--line); border-radius:6px; padding:2px 8px; color:var(--ink)}
  .kv .pill b{color:var(--muted); font-weight:600; margin-right:4px}
  .match-note{margin:4px 18px 14px; font-size:12.5px; color:var(--mid); background:var(--mid-bg); border-radius:8px; padding:8px 11px}
  .decide{display:flex; align-items:center; gap:10px; padding:13px 18px; border-top:1px solid var(--line); background:var(--panel); flex-wrap:wrap}
  .decide .q{font-size:13px; font-weight:600; color:var(--muted); margin-right:2px}
  .seg{display:inline-flex; border:1px solid var(--line); border-radius:9px; overflow:hidden}
  .seg button{appearance:none; border:0; background:var(--card); color:var(--ink); font:inherit; font-size:13.5px; font-weight:600; padding:8px 16px; cursor:pointer; border-right:1px solid var(--line); transition:background .12s,color .12s}
  .seg button:last-child{border-right:0}
  .seg button:hover{background:var(--panel)}
  .seg button:focus-visible{outline:2px solid var(--accent); outline-offset:-2px; z-index:1}
  .seg button[aria-pressed="true"][data-v="merge"]{background:var(--merge-bg); color:var(--merge)}
  .seg button[aria-pressed="true"][data-v="distinct"]{background:var(--distinct-bg); color:var(--distinct)}
  .seg button[aria-pressed="true"][data-v="unsure"]{background:var(--unsure-bg); color:var(--unsure)}
  .decided-flag{font-size:12px; color:var(--hi); font-weight:600; margin-left:auto}
  .actionbar{position:fixed; left:0; right:0; bottom:0; background:var(--card); border-top:1px solid var(--line); display:flex; align-items:center; padding:12px 20px; z-index:10}
  .actionbar .inner{max-width:980px; margin:0 auto; width:100%; display:flex; align-items:center; gap:16px}
  .progress{font-size:14px; font-weight:600}
  .progress .n{color:var(--accent)}
  .spacer{flex:1}
  .btn{appearance:none; border:1px solid var(--accent); background:var(--accent); color:var(--btn-ink); font:inherit; font-weight:650; font-size:14px; padding:9px 18px; border-radius:9px; cursor:pointer}
  .btn:hover{background:var(--accent-ink)}
  .btn:disabled{opacity:.45; cursor:not-allowed}
  .btn.ghost{background:transparent; color:var(--accent)}
  dialog{border:1px solid var(--line); border-radius:12px; padding:0; max-width:640px; width:92%; background:var(--card); color:var(--ink)}
  dialog::backdrop{background:rgba(0,0,0,.55)}
  .dlg-head{padding:14px 18px; border-bottom:1px solid var(--line); font-weight:650}
  .dlg-body{padding:16px 18px}
  .dlg-body p{margin:0 0 10px; font-size:13.5px; color:var(--muted)}
  textarea{width:100%; height:230px; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; border:1px solid var(--line); border-radius:8px; padding:10px; resize:vertical; color:var(--ink); background:var(--snip-bg)}
  .dlg-foot{padding:12px 18px; border-top:1px solid var(--line); display:flex; gap:10px; justify-content:flex-end}
  .hint{font-size:12px;color:var(--muted)}
  @media(max-width:680px){ .cols{grid-template-columns:1fr} .col:first-child{border-right:0;border-bottom:1px solid var(--line)} }
</style>"""
