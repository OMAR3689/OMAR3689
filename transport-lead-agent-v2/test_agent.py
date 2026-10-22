import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent


class AgentTests(unittest.TestCase):
    def test_official_contact_score_threshold_excludes_weak_prospects(self):
        self.assertGreaterEqual(agent.MIN_LEAD_SCORE, 40)
        self.assertLess(25, agent.MIN_LEAD_SCORE)
        self.assertLess(35, agent.MIN_LEAD_SCORE)

    def test_marketplace_listing_is_not_accepted_as_official_prospect_source(self):
        self.assertTrue(agent.is_non_prospect_source(
            "Fapaco — Fabrique Marocaine de Pâtes et Couscous",
            "Product profile and company listing",
            "https://moroccanproducts.ma/fr/company/fapaco",
        ))

    def test_logistics_service_page_is_filtered_as_provider_not_buyer(self):
        title = "Stockage & Distribution - Royas"
        content = (
            "Solutions logistiques complètes. Transport maritime et aérien, "
            "dédouanement, import/export et distribution nationale."
        )
        url = "https://www.royas.ma/fr/services/stockage-distribution"
        self.assertTrue(agent.is_bad_prospect(title, content, url))

    def test_search_web_tries_next_engine_when_first_results_are_only_directories(self):
        directory_results = ([{
            "title": "Annuaire des transporteurs au Maroc",
            "url": "https://directory.ma/liste",
            "content": "Annuaire, liste des fournisseurs transport Maroc",
        }], "ddgs")
        company_results = ([{
            "title": "Alpha Food Industries",
            "url": "https://alphafood.ma/contact",
            "content": "Fabricant agroalimentaire et distribution au Maroc",
        }], "bing-rss")
        with (
            patch.object(agent, "ddgs_search", return_value=directory_results[0]),
            patch.object(agent, "bing_rss_search", return_value=company_results[0]),
            patch.object(agent, "ddg_html_search", return_value=[]),
            patch.object(agent, "searxng_search", return_value=[]),
        ):
            results, source = agent.search_web("fabricants agroalimentaires Maroc")
        self.assertEqual(source, "bing-rss")
        self.assertEqual([item["url"] for item in results], ["https://alphafood.ma/contact"])

    def test_transient_smtp_451_is_deferred_but_auth_error_is_not_transient(self):
        self.assertTrue(
            agent.is_transient_smtp_failure(
                "{'contact@company.ma': (451, b'Temporary local problem - please try later')}"
            )
        )
        self.assertTrue(agent.is_transient_smtp_failure("(421, b'Service not available')"))
        self.assertFalse(agent.is_transient_smtp_failure("(535, b'Authentication failed')"))
        self.assertFalse(agent.is_transient_smtp_failure("SMTP credentials not configured"))

    def test_permanent_recipient_rejection_is_suppressed_but_smtp_auth_is_not(self):
        self.assertTrue(
            agent.is_permanent_recipient_rejection(
                "{'bad@company.ma': (550, b'Mailbox unavailable')}",
                "bad@company.ma",
            )
        )
        self.assertFalse(
            agent.is_permanent_recipient_rejection(
                "{'bad@company.ma': (535, b'Authentication failed')}",
                "bad@company.ma",
            )
        )
        self.assertFalse(
            agent.is_permanent_recipient_rejection(
                "{'other@company.ma': (550, b'Mailbox unavailable')}",
                "bad@company.ma",
            )
        )
        self.assertEqual(
            agent.get_permanently_rejected_emails({
                agent.PERMANENT_REJECTS_KEY: ["bad@company.ma", {"email": "dead@company.ma"}]
            }),
            {"bad@company.ma", "dead@company.ma"},
        )

    def test_retry_queue_does_not_mark_candidate_as_already_contacted(self):
        pending = [{
            "company": "Alpha Food",
            "email": "contact@alphafood.ma",
            "domain": "alphafood.ma",
            "url": "https://alphafood.ma/",
            "score": 80,
        }]
        identifiers, domains = agent.build_contact_suppression({
            agent.PENDING_RETRY_KEY: pending,
        })
        self.assertFalse(
            agent.was_contacted("contact@alphafood.ma", "alphafood.ma", identifiers, domains)
        )

    def test_history_suppression_normalizes_urls_and_mailboxes(self):
        history = {
            "https://www.example.ma/contact": {
                "email": "info@example.ma",
                "sent_at": "2026-10-20T10:00:00+00:00",
            },
            "sales@other.example": {"sent_at": "2026-10-20T11:00:00+00:00"},
        }
        identifiers, domains = agent.build_contact_suppression(history)
        self.assertTrue(agent.was_contacted("", "example.ma", identifiers, domains))
        self.assertTrue(agent.was_contacted("info@example.ma", "new-domain.ma", identifiers, domains))
        self.assertTrue(agent.was_contacted("", "sub.other.example", identifiers, domains))
        self.assertFalse(agent.was_contacted("contact@fresh.ma", "fresh.ma", identifiers, domains))

    def test_public_email_rejects_directory_page_even_if_snippet_has_company_email(self):
        results = ([{
            "title": "Company contact list",
            "url": "https://directory.example/listing/company",
            "content": "contact@company.ma",
        }], "test")
        with patch.object(agent, "search_web", return_value=results):
            self.assertEqual(agent.public_email("Company", "https://company.ma/"), ("", ""))

    def test_sendable_contact_accepts_published_role_and_named_mailboxes_on_official_https_page(self):
        valid = {
            "email": "contact@example.ma",
            "domain": "example.ma",
            "url": "https://example.ma/",
            "email_source_url": "https://example.ma/contact/",
            "score": 80,
        }
        self.assertTrue(agent.is_sendable_contact(valid))

        named_mailbox = {**valid, "email": "omar.rebbani@example.ma"}
        self.assertTrue(agent.is_sendable_contact(named_mailbox))

        export_mailbox = {**valid, "email": "export@example.ma"}
        self.assertTrue(agent.is_sendable_contact(export_mailbox))

        specialized_mailbox = {**valid, "email": "pharmacovigilance@example.ma"}
        self.assertFalse(agent.is_sendable_contact(specialized_mailbox))

        off_domain_source = {**valid, "email_source_url": "https://directory.example/contact/"}
        self.assertFalse(agent.is_sendable_contact(off_domain_source))

        http_source = {**valid, "email_source_url": "http://example.ma/contact/"}
        self.assertFalse(agent.is_sendable_contact(http_source))

        file_source = {**valid, "email_source_url": "https://example.ma/catalogue.pdf"}
        self.assertTrue(agent.is_sendable_contact(file_source))

        image_source = {**valid, "email_source_url": "https://example.ma/contact.png"}
        self.assertFalse(agent.is_sendable_contact(image_source))

    def test_official_domain_mailbox_is_preferred_over_public_gmail(self):
        records = [
            {"email": "contact.mcip@gmail.com", "source_url": "https://example.ma/contact/"},
            {"email": "contact@example.ma", "source_url": "https://example.ma/contact/"},
        ]
        with patch.object(agent.sources, "find_official_contact_records", return_value=records):
            self.assertEqual(
                agent.discover_email_record("https://example.ma/"),
                ("contact@example.ma", "https://example.ma/contact/"),
            )

        with patch.object(
            agent.sources,
            "find_official_contact_records",
            return_value=[{"email": "companycontact@gmail.com", "source_url": "https://example.ma/contact/"}],
        ):
            self.assertEqual(
                agent.discover_email_record("https://example.ma/"),
                ("companycontact@gmail.com", "https://example.ma/contact/"),
            )

    def test_public_company_gmail_can_be_sent_when_published_on_official_site(self):
        official = {
            "email": "companytransport@gmail.com",
            "domain": "example.ma",
            "url": "https://example.ma/",
            "email_source_url": "https://example.ma/contact/",
            "score": 80,
        }
        self.assertTrue(agent.is_sendable_contact(official))

        blocked_noreply = {**official, "email": "no-reply@gmail.com"}
        self.assertFalse(agent.is_sendable_contact(blocked_noreply))

        unofficial_source = {**official, "email_source_url": "https://directory.example/company/"}
        self.assertFalse(agent.is_sendable_contact(unofficial_source))

    def test_fallback_contacts_have_official_source_urls_and_pass_sendability_checks(self):
        self.assertGreaterEqual(len(agent.SEEDED_PUBLIC_CONTACTS), 30)
        for contact in agent.SEEDED_PUBLIC_CONTACTS:
            with self.subTest(company=contact["company"]):
                self.assertTrue(contact.get("email"))
                self.assertTrue(contact.get("domain"))
                self.assertTrue(contact.get("url", "").startswith("https://"))
                self.assertTrue(contact.get("email_source_url", "").startswith("https://"))
                self.assertGreaterEqual(contact.get("score", 0), 20)
                self.assertTrue(agent.is_sendable_contact(contact), contact)


    def test_search_web_keeps_com_candidate_until_official_page_geography_check(self):
        candidate = [{
            "title": "Alpha Food Industries",
            "url": "https://alphafood.example.com/contact",
            "content": "Food manufacturer and national distribution",
        }]
        with (
            patch.object(agent, "ddgs_search", return_value=candidate),
            patch.object(agent, "bing_rss_search", return_value=[]),
            patch.object(agent, "ddg_html_search", return_value=[]),
            patch.object(agent, "searxng_search", return_value=[]),
        ):
            results, source = agent.search_web("fabricants agroalimentaires Maroc")

        self.assertEqual(source, "ddgs")
        self.assertEqual(len(results), 1)
        # The result is not yet a validated lead; main() must still fetch the page
        # and verify Morocco evidence before accepting or contacting the company.
        self.assertFalse(
            agent.is_morocco_candidate(
                results[0]["title"], results[0]["content"], results[0]["url"]
            )
        )

 
    def test_lead_csv_fields_keeps_retry_and_smtp_rejection_columns(self):
        fields = agent.lead_csv_fields([
            {"company": "Alpha", "retry_reason": "451 Temporary", "retry_after": "2026-10-22T10:00:00+00:00"},
            {"company": "Beta", "smtp_rejection": "550 mailbox unavailable", "run_id": "123"},
        ])
        self.assertIn("retry_reason", fields)
        self.assertIn("retry_after", fields)
        self.assertIn("retry_count", fields)
        self.assertIn("email_verified", fields)
        self.assertIn("run_id", fields)
        self.assertIn("smtp_rejection", fields)
        self.assertEqual(len(fields), len(set(fields)))


    def test_retry_record_preserves_official_verification_and_uses_backoff(self):
        now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
        original = {
            "company": "Alpha",
            "email": "contact@alpha.ma",
            "domain": "alpha.ma",
            "url": "https://alpha.ma/",
            "email_source_url": "https://alpha.ma/contact",
            "email_verified": True,
            "score": 80,
        }
        first = agent.make_retry_record(original, "(451, temporary local problem)", now=now)
        self.assertTrue(first["email_verified"])
        self.assertEqual(first["email_source_url"], original["email_source_url"])
        self.assertEqual(first["retry_count"], 1)
        self.assertEqual(datetime.fromisoformat(first["retry_after"]), now + timedelta(minutes=5))
        self.assertFalse(agent.retry_is_due(first, now=now + timedelta(minutes=4)))
        self.assertTrue(agent.retry_is_due(first, now=now + timedelta(minutes=5)))
        second = agent.make_retry_record(first, "(451, temporary local problem)", now=now + timedelta(minutes=5))
        self.assertEqual(second["retry_count"], 2)
        self.assertEqual(datetime.fromisoformat(second["retry_after"]), now + timedelta(minutes=15))


    def test_lead_archive_enriches_no_email_company_record_without_losing_sent_history(self):
        old_rows = [{
            "company": "Alpha", "domain": "alpha.ma", "url": "https://alpha.ma/",
            "status": "no_email",
        }]
        updated_rows = [{
            "company": "Alpha", "domain": "alpha.ma", "url": "https://alpha.ma/contact",
            "email": "contact@alpha.ma", "email_verified": True, "status": "qualified", "run_id": "987",
        }]
        merged = agent.merge_lead_archive(old_rows, updated_rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["email"], "contact@alpha.ma")
        self.assertEqual(merged[0]["run_id"], "987")

        sent_row = {**updated_rows[0], "status": "sent", "sent_at": "2026-10-22T11:00:00+00:00"}
        downgraded = agent.merge_lead_archive(
            [sent_row],
            [{**updated_rows[0], "email": "", "email_verified": False, "status": "no_email"}],
        )
        self.assertEqual(len(downgraded), 1)
        self.assertEqual(downgraded[0]["status"], "sent")
        self.assertEqual(downgraded[0]["email"], "contact@alpha.ma")


    def test_tender_archive_deduplicates_official_notices_and_drops_expired_items(self):
        old_rows = [
            {
                "title": "Old transport service", "buyer": "Old Commune", "deadline": "01/01/2020",
                "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/old",
            },
            {
                "title": "Future transport service", "buyer": "New Commune", "deadline": "31/12/2099",
                "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/123",
                "status": "date_limite_future",
            },
            {
                "title": "Fake mirror", "buyer": "Unknown", "deadline": "31/12/2099",
                "url": "https://example.ma/tender/123",
            },
        ]
        new_rows = [{
            "title": "Future transport service", "buyer": "New Commune", "deadline": "31/12/2099",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/123",
            "index_engine": "Bing RSS",
        }]
        merged = agent.merge_tender_archive(old_rows, new_rows, today=date(2026, 10, 22))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "Future transport service")
        self.assertEqual(merged[0]["index_engine"], "Bing RSS")

    def test_persist_outreach_state_saves_sent_history_and_retry_queue(self):
        history = {
            "sent@example.ma": {
                "company": "Sent Co",
                "domain": "sent.ma",
                "sent_at": "2026-10-22T12:00:00+00:00",
            }
        }
        pending = {
            "later@example.ma": {
                "company": "Later Co",
                "email": "later@example.ma",
                "domain": "later.ma",
                "status": "smtp_retry_pending",
                "retry_reason": "451 temporary",
            }
        }
        rejected = {"bad@example.ma"}
        with patch.object(agent, "save_history") as save:
            agent.persist_outreach_state(history, pending, rejected)

        self.assertEqual(history[agent.PENDING_RETRY_KEY], list(pending.values()))
        self.assertEqual(history[agent.PERMANENT_REJECTS_KEY], ["bad@example.ma"])
        save.assert_called_once_with(history)


    def test_public_email_rejects_snippet_address_not_visible_on_official_page(self):
        result = [{
            "title": "Contact Alpha",
            "url": "https://alpha.ma/contact",
            "content": "contact@alpha.ma",
        }]
        with (
            patch.object(agent, "search_web", return_value=(result, "bing-rss")),
            patch.object(agent.sources, "polite_get", return_value="<html>No address here</html>"),
        ):
            self.assertEqual(agent.public_email("Alpha", "https://alpha.ma/"), ("", ""))

    def test_public_email_accepts_exact_address_visible_on_official_page(self):
        result = [{
            "title": "Contact Alpha",
            "url": "https://alpha.ma/contact",
            "content": "contact@alpha.ma",
        }]
        with (
            patch.object(agent, "search_web", return_value=(result, "bing-rss")),
            patch.object(agent.sources, "polite_get", return_value="<html>Contact: contact@alpha.ma</html>"),
        ):
            self.assertEqual(
                agent.public_email("Alpha", "https://alpha.ma/"),
                ("contact@alpha.ma", "https://alpha.ma/contact"),
            )

    def test_seed_contact_is_not_verified_without_exact_official_page_evidence(self):
        seed = {
            "email": "contact@alpha.ma",
            "url": "https://alpha.ma/",
            "email_source_url": "https://alpha.ma/contact",
        }
        with patch.object(agent.sources, "polite_get", return_value="<html>sales@alpha.ma</html>"), \
             patch.object(agent.sources, "find_official_contact_records", return_value=[]):
            self.assertEqual(
                agent.verify_public_email_source(
                    seed["email"], seed["email_source_url"], seed["url"]
                ),
                "",
            )


    def test_seed_email_can_be_verified_from_official_pdf_text(self):
        with patch.object(agent.sources, "polite_get_pdf", return_value="Contact: contact@example.ma"), \
             patch.object(agent.sources, "find_official_contact_records", return_value=[]):
            self.assertEqual(
                agent.verify_public_email_source(
                    "contact@example.ma",
                    "https://example.ma/catalogue.pdf",
                    "https://example.ma/",
                ),
                "https://example.ma/catalogue.pdf",
            )


if __name__ == "__main__":
    unittest.main()
