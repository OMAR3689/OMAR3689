# TRMB Lead Agent V2 - verified outreach

V2 researches Moroccan companies that may need transport/logistics services, records official business contact evidence, and stores public procurement notices separately from commercial prospects.

## Operating mode and safety
- The scheduled workflow is configured for a five-minute cadence; GitHub may delay scheduled runs under load.
- Scheduled runs send only to new, verified business contacts; manual workflow dispatch defaults to preview and offers an explicit send mode.
- Sending is capped at 10 emails per run and 20 per rolling hour. If there are no new verified contacts, the run succeeds without sending.
- Every candidate must have a public contact address found on its official HTTPS site. Public Gmail/Yahoo/Outlook addresses are permitted when visibly published there; role-based and named corporate mailboxes are also allowed unless they are automated, recruitment, privacy/legal, or pharmaceutical safety inboxes.
- V2 writes prospect, contact-history, and public-tender data under `transport-lead-agent-v2/data/`.
- The contact history is used to suppress repeat outreach to previously contacted addresses/domains.

## Sources and crawl etiquette
- Uses free search providers for commercial company research.
- Public Gmail/Yahoo/Outlook addresses are allowed only when listed on that company's own official website; the agent does not invent them or accept third-party directory snippets as proof.
- `sources.py` checks `robots.txt` before crawling a company or the official public-procurement portal. If robots rules cannot be read, it fails closed and skips that crawl.
- Uses a per-host delay, bounded page size, timeout and limited retries for 429/503 responses.
- No authenticated access or LinkedIn scraping.
- Official public tenders are stored separately in `tenders.json` and `tenders.csv` as opportunities for TRMB to review/bid. They are not converted into sales leads and are never emailed automatically by the tender module.
- Tender dates that are known to have passed are excluded; dates missing or not parsed are marked for manual verification.

## V2 improvements being tested
- Additional searches for explicit transport/logistics purchase intent (quotes, provider search, tenders, consultations).
- Lead metadata `direct_intent` and `priority` (`HOT`, `WARM`, `STANDARD`).
- Priority-aware ordering and expanded CSV export for review.
- More robust filtering of transport providers, company lookup sites, and tender directories.
- Separate V2 prospect/contact files from public procurement opportunities.

## Run a preview
Open GitHub Actions, select **TRMB Lead Agent V2 - Manual Dry Run**, choose branch `trmb-agent-v2`, then click **Run workflow**. Review:
- `transport-lead-agent-v2/data/leads.csv`
- `transport-lead-agent-v2/data/tenders.csv`
- the run logs

Scheduled sends are enabled through the workflow on the default branch. Review the latest workflow run and the saved CSV/JSON files after changes to scoring or contact-validation rules.
