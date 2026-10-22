import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent


class AgentTests(unittest.TestCase):
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

    def test_sendable_contact_requires_generic_email_on_official_https_page(self):
        valid = {
            "email": "contact@example.ma",
            "domain": "example.ma",
            "url": "https://example.ma/",
            "email_source_url": "https://example.ma/contact/",
            "score": 80,
        }
        self.assertTrue(agent.is_sendable_contact(valid))

        named_mailbox = {**valid, "email": "omar.rebbani@example.ma"}
        self.assertFalse(agent.is_sendable_contact(named_mailbox))

        off_domain_source = {**valid, "email_source_url": "https://directory.example/contact/"}
        self.assertFalse(agent.is_sendable_contact(off_domain_source))

        http_source = {**valid, "email_source_url": "http://example.ma/contact/"}
        self.assertFalse(agent.is_sendable_contact(http_source))

        file_source = {**valid, "email_source_url": "https://example.ma/contact.pdf"}
        self.assertFalse(agent.is_sendable_contact(file_source))

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

    def test_fallback_contacts_have_official_source_urls(self):
        self.assertGreaterEqual(len(agent.SEEDED_PUBLIC_CONTACTS), 20)
        for contact in agent.SEEDED_PUBLIC_CONTACTS:
            with self.subTest(company=contact["company"]):
                self.assertTrue(contact.get("email"))
                self.assertTrue(contact.get("domain"))
                self.assertTrue(contact.get("url", "").startswith("https://"))
                self.assertTrue(contact.get("email_source_url", "").startswith("https://"))
                self.assertGreaterEqual(contact.get("score", 0), 20)


if __name__ == "__main__":
    unittest.main()
