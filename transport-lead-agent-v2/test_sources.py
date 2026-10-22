import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources


class SourcesTests(unittest.TestCase):
    def setUp(self):
        sources._robots_cache.clear()
        sources._last_hit.clear()

    def test_missing_robots_txt_does_not_block_all_crawling(self):
        error = urllib.error.HTTPError(
            "https://example.ma/robots.txt", 404, "Not Found", {}, None
        )
        with patch("sources.urllib.request.urlopen", side_effect=error):
            self.assertTrue(sources.allowed_by_robots("https://example.ma/contact"))

    def test_forbidden_robots_txt_fails_closed(self):
        error = urllib.error.HTTPError(
            "https://example.ma/robots.txt", 403, "Forbidden", {}, None
        )
        with patch("sources.urllib.request.urlopen", side_effect=error):
            self.assertFalse(sources.allowed_by_robots("https://example.ma/contact"))

    def test_robots_timeout_fails_closed(self):
        with patch("sources.urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            self.assertFalse(sources.allowed_by_robots("https://example.ma/contact"))

    def test_only_same_domain_generic_business_emails_are_extracted(self):
        html = """
        contact@example.ma info@example.ma
        pharmacovigilance@example.ma
        contact@agency.example
        contact@other.ma
        contact@img.example.ma.png
        """
        emails = sources.extract_generic_emails(html, "example.ma")
        self.assertEqual(emails, ["contact@example.ma", "info@example.ma"])

    def test_tender_parser_extracts_future_transport_opportunity(self):
        row = {
            "text": (
                "Objet : Transport et livraison de marchandises "
                "Acheteur public : Commune Exemple "
                "Date limite de remise des plis : 31/12/2099"
            ),
            "links": [
                ("/index.php?page=entreprise.EntrepriseDetailConsultation&id=123", "Détail")
            ],
        }
        tender = sources._parse_tender_row(
            row, "transport", "https://www.marchespublics.gov.ma/index.php"
        )
        self.assertIsNotNone(tender)
        self.assertEqual(tender["buyer"], "Commune Exemple")
        self.assertEqual(tender["deadline"], "31/12/2099")
        self.assertEqual(tender["status"], "date_limite_future")
        self.assertEqual(tender["source"], "Portail Marocain des Marchés Publics")

    def test_indexed_tender_parser_accepts_official_portal_result(self):
        item = {
            "title": "Avis de consultation - Transport de marchandises",
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailConsultation&id=123",
            "content": "Acheteur public : Commune Exemple Date limite de remise des plis : 31/12/2099",
        }
        tender = sources._parse_indexed_tender(item, "transport")
        self.assertIsNotNone(tender)
        self.assertEqual(tender["buyer"], "Commune Exemple")
        self.assertEqual(tender["deadline"], "31/12/2099")
        self.assertEqual(tender["status"], "date_limite_future")
        self.assertIn("Index Bing", tender["source"])

    def test_indexed_tender_parser_rejects_external_directory_results(self):
        item = {
            "title": "Transport de marchandises - appel d'offres",
            "url": "https://example-directory.ma/tender/123",
            "content": "Date limite 31/12/2099",
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "transport"))

    def test_indexed_tender_parser_rejects_expired_deadline(self):
        item = {
            "title": "Consultation transport marchandises",
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailConsultation&id=123",
            "content": "Date limite de remise des plis : 01/01/2000",
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "transport"))

    def test_official_email_record_includes_exact_source_page(self):
        html = "<html><body>info@example.ma</body></html>"
        with patch("sources.polite_get", return_value=html):
            records = sources.find_official_contact_records("https://example.ma/contact")
        self.assertEqual(records, [{
            "email": "info@example.ma",
            "source_url": "https://example.ma/contact",
        }])

    def test_tender_fallback_uses_ddgs_when_official_portal_is_unreachable(self):
        item = {
            "title": "Avis de consultation - Transport de marchandises",
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailConsultation&id=456",
            "content": "Acheteur public : Commune Exemple Date limite de remise des plis : 31/12/2099",
        }
        with patch("sources.polite_get", return_value=None), \
             patch("sources._ddgs_index_search", return_value=[item]), \
             patch("sources._bing_rss_index_search", return_value=[]), \
             patch("sources._duckduckgo_html_index_search", return_value=[]):
            tenders = sources.find_tenders(keywords=("transport",))
        self.assertEqual(len(tenders), 1)
        self.assertEqual(tenders[0]["index_engine"], "DDGS")
        self.assertEqual(tenders[0]["buyer"], "Commune Exemple")

    def test_duckduckgo_html_parser_extracts_public_result_link(self):
        html = (
            '<a class="result__a" href="https://www.marchespublics.gov.ma/'
            'index.php?page=entreprise.EntrepriseDetailConsultation&amp;id=456">'
            'Avis de consultation transport</a>'
        )
        parser = sources._SearchResultLinkParser()
        parser.feed(html)
        self.assertEqual(len(parser.results), 1)
        self.assertIn("marchespublics.gov.ma", parser.results[0]["url"])
        self.assertIn("transport", parser.results[0]["title"].lower())


if __name__ == "__main__":
    unittest.main()
