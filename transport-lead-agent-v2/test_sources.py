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


if __name__ == "__main__":
    unittest.main()
