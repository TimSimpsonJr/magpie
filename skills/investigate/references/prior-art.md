# investigate -- citation anchor prior art

The magpie citation anchor is mostly an invented format layered over a small set
of Phase-6-verified docling facts. This note records the three pieces of prior
art the format rests on, so a future reader can tell what was borrowed from what
was invented. No new library dependencies: the engine (scripts/citation.py) is
pure stdlib and resolves over a plain json.load dict.

## (a) The verified docling-core 2.78.1 serialized shape

Verified at the Phase-6 ingest research gate (skills/ingest/references) and
re-confirmed against the pinned docling-core 2.78.1 in the venv. ingest writes a
DoclingDocument via save_as_json (schema 1.10.0). The anchor resolves over the
top-level texts[] array, where each item carries:

- self_ref -- the stable in-document ref string "#/texts/{i}".
- text -- the sanitized surface. This is the ONE canonical surface the anchor
  matches against.
- orig -- the untreated surface. The anchor NEVER matches against orig.
- prov -- a LIST of provenance fragments, each
  {page_no, bbox:{l,t,r,b,coord_origin}, charspan:[start,end)}.

Key fact: prov[].charspan spans the WHOLE item, not a sub-span, so the anchor
computes its own [char_start, char_end) sub-offsets into a block's .text rather
than reusing charspan. See finding (c).

## (b) The W3C TextQuoteSelector (prefix / exact / suffix) prior art

The relocation context is the one genuinely borrowed design idea. The W3C Web
Annotation Data Model defines a TextQuoteSelector that locates a span by an exact
quote plus a small prefix and suffix window of surrounding text. The magpie
anchor stores context_prefix and context_suffix (a fixed-width window of the
block's .text immediately before and after the quote) for exactly this purpose:
when stored char offsets shift (an OCR re-run), the resolver re-locates the quote
by its text plus its prefix/suffix context. Storing the context at build time and
requiring it at relocation is what keeps the relocated level safe -- it stops a
short quote from relocating into the interior of a larger token elsewhere and
disambiguates a repeated span. Everything else (the full sha256 integrity hash,
the single-prov geometry rule, the degrading fallback levels) is a magpie
invention, not part of the W3C selector.

## (c) The early Greenville-RFP validation finding

The invented format was validated early against a real public record (City of
Greenville RFP No. 21-3746 / LPR FOIA response, used locally, never committed).
On the first 12 native pages: 189 single-prov text blocks, 0 multi-prov, and
6 of 189 blocks where prov.charspan was NOT [0, len(text)).

That finding drove two format decisions:

1. prov.charspan is NOT a reliable .text offset, so build_anchor computes its own
   [char_start, char_end) by locating the verbatim_quote inside the block's
   .text, and resolve_anchor slices .text with those self-computed offsets.
   Resolution never reads prov.charspan.
2. The v1 anchor requires the quoted block to be single-prov (n_prov == 1). A
   single-prov block lets page_no and bbox be taken straight from prov[0] as
   faithful scalars; a multi-prov block is rejected at build time and degrades at
   resolve time (geometry dropped) rather than reporting a faux-precise prov[0].

These two rules sidestep the charspan-to-coordinate question entirely and keep
the anchor deterministic and golden-testable.
