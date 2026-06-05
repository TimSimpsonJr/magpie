# corpus/public -- Provenance Datasheet

Provenance + PII record for every bundled public artifact. A FOIA/PII tool carries
its own data's provenance. Nothing here is committed until it is PII-clean and (for
re-hosted third-party data) permission is recorded.

## <slice-filename>.csv  (RANGE Media -- Spokane County Flock network audit slice)
- Source URL: <RANGE public data page + the Google Drive link>
- Attribution: RANGE Media (https://www.rangemedia.co/public-flock-data/)
- License / permission: re-host permission GRANTED by RANGE on <DATE> (record kept
  out-of-repo). The full release carries no stated license; we re-host a slice with
  permission. NOT public domain.
- Slice rule: tools/build_public_slice.py, stable total-order sort by (Org Name,
  Search Time, <rest>), first N=<N> rows, drop-all-empty-rows. Reproducible.
- PII-scrub: pii_sweep over the `reason` column on <DATE>; result <clean | redacted
  via redact_output | reason column dropped>. Re-scan confirmed clean.
- sha256: <final sha256 of the committed slice>
- Status: <pending permission | bundled>

## <skokie-filename>.pdf  (Skokie PD -- Flock FOIA cover letter)
- Source URL: <MuckRock request URL + the CDN PDF URL>
- Attribution: Skokie Police Department, via MuckRock.
- License / permission: government record published on MuckRock (public-by-default);
  attribution to MuckRock + the agency.
- PII-scrub: ingest -> redaction_check (received) + pii_sweep over extracted text on
  <DATE>; human eyeball. Result <clean | not bundled>.
- sha256: <final sha256 of the committed PDF>
- Status: <pending vetting | bundled>
