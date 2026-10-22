# TRMB Lead Agent V2 - isolated preview

This is a separate development copy of the working V1 agent.

## Safety and isolation
- V1 remains unchanged on `main`; its five-minute run chain is untouched.
- V2 runs only on branch `trmb-agent-v2`.
- V2 defaults to `DRY_RUN=true`: it collects prospects and previews planned messages but does not submit email.
- The workflow intentionally does not provide SMTP credentials, adding a second barrier against accidental sending.
- V2 writes independent prospect, contact-history, and public-tender data under `transport-lead-agent-v2/data/`.
- Each run imports the latest V1 `contacted.json` as a suppression history so the preview skips addresses and domains already contacted by V1.

## Sources and crawl etiquette
- Uses free search providers for commercial company research.
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

Do not enable outbound sending until the preview quality has been reviewed and any V1/V2 suppression-history race condition is prevented. The present workflow intentionally sends no email.
