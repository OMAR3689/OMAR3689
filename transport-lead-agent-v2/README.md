# TRMB Lead Agent V2 - verified outreach

V2 researches Moroccan companies that may need transport/logistics services, records official business contact evidence, and stores public procurement notices separately from commercial prospects.

## Operating mode and safety
- Manual workflow dispatch defaults to preview and offers an explicit send mode.
- After an intentional, successful send-mode activation, each validated send run queues the next V2 cycle with a 90-second pause; actual cadence depends on the Actions run duration and GitHub queueing. The workflow does not guarantee an exact one-minute interval.
- Sending is capped at 10 emails per run and 20 per rolling hour. If there are no new verified contacts, the run succeeds without sending. Explicit SMTP 4xx temporary rejections are put in a persistent retry queue and retried in the next cycle; permanent/non-transient failures remain visible as run errors.
- Every contact must be visibly found on an official HTTPS company page; a search-engine snippet alone is not sufficient. Placeholder/test addresses such as `exemple@gmail.com` are rejected, and the validation stage requires the explicit `email_verified=true` evidence flag.
- The scoring rubric distinguishes direct stated transport intent from ordinary sector fit, so generic manufacturer pages no longer all receive a saturated score of 100. Public Gmail/Yahoo/Outlook addresses are permitted when visibly published there; role-based and named corporate mailboxes are also allowed unless they are automated, recruitment, privacy/legal, or pharmaceutical safety inboxes.
- V2 writes prospect, contact-history, and public-tender data under `transport-lead-agent-v2/data/`.
- Lead and tender exports are cumulative archives with duplicate company/notice merging; expired dated tender notices are removed. Each lead row carries `run_id` so workflow validation checks only the current run while retaining prior records.
- Temporary SMTP 4xx rejects retain the verified official-page evidence and use a bounded exponential retry schedule (5 minutes, 10 minutes, 20 minutes, up to six hours). Legacy retry rows without verification evidence are re-verified against the official HTTPS page before being eligible for sending.
- The contact history is used to suppress repeat outreach to previously contacted addresses/domains.

## Sources and crawl etiquette
- Uses free search providers for commercial company research.
- Public Gmail/Yahoo/Outlook addresses are allowed only when listed on that company's own official website; the agent does not invent them or accept third-party directory snippets as proof.
- `sources.py` checks `robots.txt` before crawling a company or the official public-procurement portal. If robots rules cannot be read, it fails closed and skips that crawl.
- Uses a per-host delay, bounded page size, timeout and limited retries for 429/503 responses.
- No authenticated access or LinkedIn scraping.
- Official public tenders are stored separately in `tenders.json` and `tenders.csv` as opportunities for TRMB to review/bid. They are not converted into sales leads and are never emailed automatically by the tender module.
- Tender dates that are known to have passed are excluded; missing/unparsed deadlines are clearly marked for manual verification and are not presented as confirmed open tenders.

## V2 improvements being tested
- Additional searches for explicit transport/logistics purchase intent (quotes, provider search, tenders, consultations).
- Lead metadata `direct_intent` and `priority` (`HOT`, `WARM`, `STANDARD`).
- Priority-aware ordering and expanded CSV export for review.
- More robust filtering of transport providers, company lookup sites, and tender directories.
- Search providers are retried when an engine returns only directories or competitors; Moroccan .com candidates are retained until the official page is fetched and its Morocco evidence is checked. The official-contact fallback list is expanded with additional Moroccan industrial distributors, manufacturers and B2B suppliers.
- Search-index public procurement titles are cleaned using the official notice object so metadata is not mislabeled as the tender subject.
- When the official portal is unreachable, tender discovery uses six short, targeted public-index queries instead of an oversized combined OR query; results remain limited to official notice-detail URLs.
- Separate V2 prospect/contact files from public procurement opportunities.
- Additional research queries cover Moroccan flour mills, confectionery manufacturers, pasta/couscous producers, and canneries; official contacts are verified again at runtime before use. Prospects must score at least 60/100 before outreach. Marketplace/directory domains are blocked as company, contact-source, or recipient domains. In send mode, a public DNS-over-HTTPS check requires an MX record or a valid implicit-MX A/AAAA route before SMTP is attempted; if DNS is unavailable, that recipient is held for a later cycle. This confirms domain-level mail routing only, not the existence of an individual mailbox; permanent SMTP recipient rejections are recorded and suppressed.

## Run a preview
Open GitHub Actions, select **TRMB Lead Agent V2 - Manual Dry Run**, choose branch `trmb-agent-v2`, then click **Run workflow**. Review:
- `transport-lead-agent-v2/data/leads.csv`
- `transport-lead-agent-v2/data/tenders.csv`
- the run logs

No scheduled sending trigger is configured in this V2 workflow. Code pushes tagged `[preview-v2]` run discovery in `DRY_RUN=true` and do not send emails. Sending requires an explicit manual workflow-dispatch selection of `send` or a deliberately reviewed `[send-v2]` activation commit; the workflow runs syntax/unit tests first, verifies official contact-source URLs and records outcomes before considering a cycle valid. Keep previews as the default until the saved leads and delivery policy are reviewed.
