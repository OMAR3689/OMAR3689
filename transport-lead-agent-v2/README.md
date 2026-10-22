# TRMB Lead Agent V2 - isolated preview

This is a separate development copy of the working V1 agent.

## Safety and isolation
- V1 remains unchanged on `main`; its five-minute run chain is untouched.
- V2 runs only when manually dispatched on branch `trmb-agent-v2`.
- V2 defaults to `DRY_RUN=true`: it collects prospects and previews planned messages but does not submit email.
- The workflow intentionally does not provide SMTP credentials, adding a second barrier against accidental sending.
- V2 writes its own files under `transport-lead-agent-v2/data/`.
- Each V2 run imports the latest V1 `contacted.json` as suppression history so the preview skips already-contacted addresses/domains.

## Improvements being tested
- Additional searches for explicit transport/logistics purchase intent (quotes, provider search, tenders, consultations).
- Lead metadata `direct_intent` and `priority` (`HOT`, `WARM`, `STANDARD`).
- Priority-aware ordering and expanded CSV export for review.
- Separate V2 prospect and outreach history files.

## Run a preview
Open GitHub Actions, select **TRMB Lead Agent V2 - Manual Dry Run**, choose branch `trmb-agent-v2`, then click **Run workflow**. Review `transport-lead-agent-v2/data/leads.csv` and the run logs.

Do not enable outbound sending until suppression history is shared safely between V1 and V2 and the preview quality has been reviewed.
