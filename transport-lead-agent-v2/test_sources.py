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

    def test_public_company_gmail_is_allowed_only_when_published_on_official_site(self):
        html = """
        contact@example.ma info@example.ma
        companylogistique@gmail.com
        no-reply@gmail.com
        contact@other.ma
        """
        emails = sources.extract_generic_emails(html, "example.ma")
        self.assertEqual(
            emails,
            ["companylogistique@gmail.com", "contact@example.ma", "info@example.ma"],
        )

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
        self.assertIn("Index public", tender["source"])

    def test_indexed_tender_rejects_unrelated_specific_title_even_if_category_matches(self):
        item = {
            "title": "Acquisition des switchs pour équiper le siège de l'ANCFCC",
            "url": "https://www.marchespublics.gov.ma/?page=entreprise.EntrepriseDetailsConsultation&refConsultation=1023119",
            "content": "Catégorie principale : Transport et logistique. Acheteur public : ANCFCC",
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "flotte"))

    def test_indexed_tender_rejects_generic_purchase_with_no_buyer_or_relevant_object(self):
        item = {
            "title": "Détails de l'avis d'achat #48/2026",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/386893",
            "content": "Date mise en ligne : 15/10/2026",
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "livraison"))

    def test_indexed_tender_rejects_generic_purchase_when_only_buyer_is_known(self):
        item = {
            "title": "Détails de l'avis d'achat #68/2026",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/394750",
            "content": (
                "Acheteur public : DIRECTEUR REGIONAL DU MINISTERE DE LA JEUNESSE "
                "ET DES SPORTS REGION SOUSS - MASSA"
            ),
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "transport"))

    def test_indexed_tender_rejects_switch_purchase_when_portal_metadata_matches_fleet(self):
        item = {
            "title": "Marchés publics électroniques",
            "url": "https://www.marchespublics.gov.ma/?page=entreprise.EntrepriseDetailsConsultation&refConsultation=1023119&orgAcronyme=q1s",
            "content": (
                "Objet : Acquisition des switchs pour équiper le siège de l'ANCFCC "
                "Acheteur public : AGENCE NATIONALE DE LA CONSERVATION FONCIERE "
                "Catégorie principale : Transport et logistique"
            ),
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "flotte"))

    def test_indexed_tender_rejects_delivery_of_communication_materials(self):
        item = {
            "title": "Marchés publics électroniques",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/381414",
            "content": (
                "Objet : Conception, impression et livraison de supports de communication "
                "pour la promotion du Pass Jeunes "
                "Acheteur public : DIRECTEUR REGIONAL DU MINISTERE DE LA JEUNESSE "
                "Catégorie principale : Livraison"
            ),
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "livraison"))

    def test_indexed_tender_rejects_delivery_and_installation_of_air_conditioners(self):
        item = {
            "title": "Marchés publics électroniques",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/378751",
            "content": (
                "Objet : Acquisition, livraison, installation et mise en service de climatiseurs "
                "Acheteur public : FACULTE DES SCIENCES JURIDIQUES ECONOMIQUES ET SOCIALES AGDAL"
            ),
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "livraison"))

    def test_generic_purchase_rejects_air_conditioners_even_when_delivery_is_mentioned(self):
        item = {
            "title": "Détails de l'avis d'achat #48/2026",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/378751",
            "content": (
                "Objet Acquisition, livraison, installation et mise en service de climatiseurs "
                "Acheteur public : FACULTE DES SCIENCES JURIDIQUES ECONOMIQUES ET SOCIALES AGDAL"
            ),
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "livraison"))

    def test_delivery_word_alone_does_not_qualify_as_transport_service(self):
        item = {
            "title": "Avis de consultation - Livraison de lots de fournitures de bureau",
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailConsultation&id=987",
            "content": "Acheteur public : Commune Exemple Date limite de remise des plis : 31/12/2099",
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "livraison"))

    def test_generic_purchase_with_transport_evidence_gets_descriptive_title(self):
        item = {
            "title": "Détails de l'avis d'achat #19/EXP/2026",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/394009",
            "content": (
                "Transport de marchandises Acheteur public : "
                "DIRECTION PROVINCIALE DE L'EDUCATION NATIONALE D'OUJDA ANGAD "
                "Date mise en ligne 15/10/2026 09:46"
            ),
        }
        tender = sources._parse_indexed_tender(item, "transport")
        self.assertIsNotNone(tender)
        self.assertEqual(tender["title"], "Transport de marchandises")
        self.assertEqual(
            tender["buyer"],
            "DIRECTION PROVINCIALE DE L'EDUCATION NATIONALE D'OUJDA ANGAD",
        )

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

    def test_indexed_tender_prefers_object_and_parses_buyer_cleanly(self):
        item = {
            "title": "Marchés publics électroniques",
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailsConsultation&refConsultation=1044305",
            "content": (
                "Objet : Transport de marchandises entre sites. "
                "Acheteur public : M6 / CTSA - Casablanca Transport en Site Aménagé SA "
                "Type d'annonce : Annonce de consultation "
                "Procédure : Appel d'offres ouvert "
                "Date limite de remise des plis : 31/12/2099"
            ),
        }
        tender = sources._parse_indexed_tender(item, "transport")
        self.assertIsNotNone(tender)
        self.assertEqual(tender["title"], "Transport de marchandises entre sites.")
        self.assertEqual(tender["buyer"], "M6 / CTSA - Casablanca Transport en Site Aménagé SA")
        self.assertEqual(tender["deadline"], "31/12/2099")
        self.assertEqual(tender["source"], "Index public du Portail Marocain des Marchés Publics")

    def test_indexed_tender_does_not_mistake_publication_date_for_deadline(self):
        item = {
            "title": "Avis de consultation - Transport de marchandises",
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailConsultation&id=789",
            "content": "Publication : 01/01/2025. Acheteur public : Commune Exemple",
        }
        tender = sources._parse_indexed_tender(item, "transport")
        self.assertIsNotNone(tender)
        self.assertEqual(tender["deadline"], "")
        self.assertEqual(tender["status"], "date_limite_inconnue_a_verifier")

    def test_tender_parser_rejects_generic_search_listing_url(self):
        item = {
            "title": (
                "CONCOURS ARCHITECTURAL POUR LA CONCEPTION ET LE SUIVI DES TRAVAUX "
                "DE CONSTRUCTION DU NOUVEAU SIEGE DE LA DIRECTION REGIONALE DE "
                "L'EQUIPEMENT, DU TRANSPORT ET DE LA LOGISTIQUE"
            ),
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseAdvancedSearch&WithCritere&searchAnnCons",
            "content": "Catégorie principale : Transport et logistique",
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "logistique"))

    def test_tender_parser_rejects_generic_portal_title_without_object(self):
        item = {
            "title": "Marchés publics électroniques",
            "url": "https://www.marchespublics.gov.ma/?page=entreprise.EntrepriseDetailsConsultation&refConsultation=1044305",
            "content": "Acheteur public : Commune Exemple Type d'annonce : Annonce de consultation",
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "transport"))

    def test_buyer_field_excludes_publication_date_metadata(self):
        item = {
            "title": "Détails de l'avis d'achat #19/EXP/2026",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/394009",
            "content": (
                "Transport de marchandises Acheteur public : "
                "DIRECTION PROVINCIALE DE L'EDUCATION NATIONALE D'OUJDA ANGAD "
                "Date mise en ligne 15/10/2026 09:46"
            ),
        }
        tender = sources._parse_indexed_tender(item, "transport")
        self.assertIsNotNone(tender)
        self.assertEqual(
            tender["buyer"],
            "DIRECTION PROVINCIALE DE L'EDUCATION NATIONALE D'OUJDA ANGAD",
        )
        self.assertNotIn("Date mise en ligne", tender["buyer"])

    def test_official_purchase_detail_without_object_evidence_is_rejected(self):
        item = {
            "title": "Détails de l'avis d'achat #19/EXP/2026",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/394009",
            "content": (
                "Acheteur public : DIRECTION PROVINCIALE DE L'EDUCATION NATIONALE "
                "D'OUJDA ANGAD Date mise en ligne 15/10/2026 09:46"
            ),
        }
        self.assertIsNone(sources._parse_indexed_tender(item, "transport"))


if __name__ == "__main__":
    unittest.main()
