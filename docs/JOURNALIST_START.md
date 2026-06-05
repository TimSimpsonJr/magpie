# Getting started with Magpie

Welcome. Magpie is a research assistant for working with documents and data you
get from public-records requests. You talk to it in plain language inside Claude
Code -- there is nothing to install, configure, or keep running on your end.
Whoever set up this machine has already handled all of that.

This page is the two-minute tour: what Magpie can do for you, how to ask, and what
to do if something looks off.

---

## What Magpie does for you

Think of Magpie as a careful research partner that never gets tired of the boring,
exacting parts of an investigation. You can ask it to:

- **Make sense of a FOIA spreadsheet.** Point it at a CSV or Excel release and ask
  what is in it -- totals, patterns, outliers, who paid whom, what changed over
  time.
- **Read a scanned PDF release.** Hand it a document dump, even an ugly scan, and
  it turns the pages into clean, quotable text you can actually search and cite.
- **Sweep for personal information.** Ask it to find exposed names and other
  personal details in a release -- useful both for spotting what an agency failed
  to redact and for protecting uninvolved people before you publish.
- **Check a redaction.** Give it a redacted PDF and ask whether any of the black
  boxes are hiding text that is still selectable underneath.
- **Get a citable claim.** Ask it to tie a statement back to the exact place in a
  source document it came from, so your reporting is anchored to evidence.
- **Timestamp evidence you receive.** Ask it to record proof of exactly what a
  file was and when it arrived, so you can show a document has not changed since
  you got it.

You do not need to know which of these are "ready" before you start -- just ask,
and Magpie will tell you if something it needs is not set up yet.

---

## How to use it

Open Claude Code in this project and ask for what you want in ordinary words. You
do not need special commands or jargon. For example:

- "Here is a spreadsheet from the city. What stands out in the payments column?"
- "Read this scanned PDF and pull out every date and dollar amount you can find."
- "Are there any names in this release that should have been redacted?"

Magpie figures out which tool to reach for. If it needs a moment to read a big
document the first time, that is normal -- it is getting set up to read fast from
then on.

---

## If something looks off

If Magpie says it cannot do something, or a result seems incomplete, run the
**`doctor`** skill. Just ask Claude Code to run **doctor**. It is a quick, read-only
check -- it changes nothing on the machine -- and it reports, in the same plain
terms used above, which kinds of work are ready right now and which are not.

If **doctor** reports that something is missing, you do not have to fix it
yourself. Pass the word to whoever set this machine up for you and ask them to run
**setup**. That is their job, and it is a one-time thing. Once they have, run
**doctor** again to confirm you are good to go.

That is everything you need. Ask Magpie a question and start digging.
