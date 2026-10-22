import sys
import unittest
import urllib.error
from io import BytesIO
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

    def test_public_procurement_keywords_cover_common_transport_service_notices(self):
        expected = {
            "transport",
            "logistique",
            "prestations de transport",
            "transport du personnel",
            "organisation logistique",
            "location de camions",
            "affrètement",
        }
        self.assertTrue(expected.issubset(set(sources.MP_KEYWORDS)))

    def test_forbidden_robots_txt_fails_closed(self):
        error = urllib.error.HTTPError(
            "https://example.ma/robots.txt", 403, "Forbidden", {}, None
        )
        with patch("sources.urllib.request.urlopen", side_effect=error):
            self.assertFalse(sources.allowed_by_robots("https://example.ma/contact"))

    def test_robots_timeout_fails_closed(self):
        with patch("sources.urllib.request.urlopen", side_effect=TimeoutError("timeout")), \\
             patch("sources.time.sleep"):
            self.assertFalse(sources.allowed_by_robots("https://example.ma/contact"))

    def test_robots_transient_timeout_retries_once_then_respects_published_rules(self):
        allowed_rules = BytesIO(b"User-agent: *\\nAllow: /\\n")
        with patch(
            "sources.urllib.request.urlopen",
            side_effect=[TimeoutError("transient timeout"), allowed_rules],
        ) as mocked, patch("sources.time.sleep"):
            self.assertTrue(sources.allowed_by_robots("https://example.ma/contact"))
            self.assertEqual(mocked.call_count, 2)

    def test_public_business_emails_from_official_domain_include_role_and_named_addresses(self):
        html = """
        contact@example.ma info@example.ma export@example.ma malika.ouchhal@example.ma
        pharmacovigilance@example.ma jobs@example.ma
        contact@agency.example
        contact@other.ma
        contact@img.example.ma.png
        """
        emails = sources.extract_generic_emails(html, "example.ma")
        self.assertEqual(
            emails,
            [
                "contact@example.ma",
                "export@example.ma",
                "info@example.ma",
                "malika.ouchhal@example.ma",
            ],
        )

    def test_placeholder_emails_are_never_valid_business_contacts(self):
        for email in (
            "exemple@gmail.com",
            "example@example.ma",
            "test@company.ma",
            "demo@gmail.com",
            "sample@company.ma",
        ):
            with self.subTest(email=email):
                self.assertFalse(
                    sources.is_public_business_email(
                        email, site_domain="company.ma", allow_external_domain=True
                    )
                )

    def test_public_business_email_validator_blocks_non_sales_mailboxes(self):
        self.assertTrue(sources.is_public_business_email("export@example.ma", "example.ma"))
        self.assertTrue(sources.is_public_business_email("malika.ouchhal@example.ma", "example.ma"))
        self.assertFalse(sources.is_public_business_email("pharmacovigilance@example.ma", "example.ma"))
        self.assertFalse(sources.is_public_business_email("jobs@example.ma", "example.ma"))
        self.assertFalse(sources.is_public_business_email("suivis@example.ma", "example.ma"))
        self.assertFalse(sources.is_public_business_email("tracking@example.ma", "example.ma"))
        self.assertTrue(
            sources.is_public_business_email("contact.mcip@gmail.com", "example.ma")
        )
        self.assertFalse(
            sources.is_public_business_email("contact@other.ma", "example.ma")
        )

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

    def test_indexed_tender_parser_cleans_mixed_object_metadata_from_title(self):
        item = {
            "title": (
                "#29/BG/2026 Objet TRANSPORT DE MATERIEL DIVERS. "
                "statuts obligatoires pour vérifier l'activité Détails Acheteur publicGOUVERNEUR"
            ),
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/394682",
            "content": (
                "#29/BG/2026 Objet : TRANSPORT DE MATERIEL DIVERS. "
                "Statuts obligatoires pour vérifier l'activité. "
                "Acheteur public : GOUVERNEUR DE LA PROVINCE D'AOUSSERD"
            ),
        }
        tender = sources._parse_indexed_tender(item, "transport")
        self.assertIsNotNone(tender)
        self.assertEqual(tender["title"], "TRANSPORT DE MATERIEL DIVERS")

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

    def test_contact_crawler_visits_contact_page_even_when_homepage_has_an_email(self):
        def fake_get(url):
            if url == "https://example.ma/":
                return (
                    '<p>info@example.ma</p>'
                    '<a href="/contact">Contact</a>'
                )
            if url == "https://example.ma/contact":
                return "<p>commercial@example.ma</p>"
            return None

        with patch("sources.polite_get", side_effect=fake_get):
            records = sources.find_official_contact_records(
                "https://example.ma/", max_pages=4
            )

        self.assertIn("info@example.ma", [item["email"] for item in records])
        self.assertIn("commercial@example.ma", [item["email"] for item in records])
        source_map = {item["email"]: item["source_url"] for item in records}
        self.assertEqual(source_map["commercial@example.ma"], "https://example.ma/contact")

    def test_official_email_record_includes_exact_source_page(self):
        html = "<html><body>info@example.ma</body></html>"
        with patch("sources.polite_get", return_value=html):
            records = sources.find_official_contact_records("https://example.ma/contact")
        self.assertEqual(records, [{
            "email": "info@example.ma",
            "source_url": "https://example.ma/contact",
        }])

    def test_direct_portal_parser_rejects_delivery_incidental_to_air_conditioners(self):
        html = """
        <table><tr>
          <td>Objet : Acquisition, livraison, installation et mise en service de climatiseurs</td>
          <td>Acheteur public : Faculté des Sciences juridiques, économiques et sociales Agdal</td>
          <td>Date limite de remise des plis : 31/12/2099</td>
          <td><a href="/index.php?page=entreprise.EntrepriseDetailsConsultation&refConsultation=378751">Détail consultation</a></td>
        </tr></table>
        """
        with patch("sources.polite_get", return_value=html),              patch("sources._find_indexed_tenders", return_value=[]):
            tenders = sources.find_tenders(keywords=("livraison",))
        self.assertEqual(tenders, [])

    def test_transport_service_title_is_kept_by_central_gate(self):
        tender = {"title": "Transport de marchandises entre sites"}
        self.assertTrue(sources._is_transport_service_tender(tender))
        self.assertFalse(
            sources._is_transport_service_tender(
                {"title": "Acquisition, livraison et installation de climatiseurs"}
            )
        )

    def test_grouped_tender_search_matches_keyword_from_notice_object(self):
        item = {
            "title": "Marchés publics électroniques",
            "url": "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/show/456789",
            "content": (
                "Objet : Organisation de la logistique afférente aux missions "
                "Business to Business à l’extérieur du Royaume "
                "Acheteur public : MINISTERE DE L'INDUSTRIE ET DU COMMERCE "
                "Date limite de remise des plis : 31/12/2099"
            ),
        }
        keyword = sources._keyword_for_indexed_tender(
            item, sources.MP_KEYWORDS
        )
        self.assertEqual(keyword, "organisation logistique")
        tender = sources._parse_indexed_tender(item, keyword)
        self.assertIsNotNone(tender)
        self.assertIn("logistique", tender["title"].lower())

    def test_tender_fallback_uses_ddgs_when_official_portal_is_unreachable(self):
        item = {
            "title": "Avis de consultation - Transport de marchandises",
            "url": "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailConsultation&id=456",
            "content": "Acheteur public : Commune Exemple Date limite de remise des plis : 31/12/2099",
        }
        with patch("sources.polite_get", return_value=None), \
             patch("sources._ddgs_index_search", return_value=[item]) as ddgs, \
             patch("sources._bing_rss_index_search", return_value=[]), \
             patch("sources._duckduckgo_html_index_search", return_value=[]):
            tenders = sources.find_tenders(keywords=("transport",))
        self.assertEqual(len(tenders), 1)
        self.assertEqual(tenders[0]["index_engine"], "DDGS")
        self.assertEqual(tenders[0]["buyer"], "Commune Exemple")
        self.assertLessEqual(
            ddgs.call_count, len(sources.MP_INDEX_QUERY_VARIANTS),
            "indexed fallback should use bounded targeted query groups",
        )
        self.assertTrue(
            all(len(call.args[0]) < 250 for call in ddgs.call_args_list),
            "procurement fallback should not send oversized OR queries",
        )
        self.assertIn(
            '"transport de marchandises"',
            " ".join(sources.MP_INDEX_QUERY_VARIANTS),
            "procurement fallback should search a specific freight-service phrase",
        )

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
        self.assertEqual(tender["title"], "Transport de marchandises entre sites")
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


    def test_contact_crawler_extracts_email_from_official_catalog_pdf(self):
        home = '<html><a href="/documents/catalogue-2026.pdf">Catalogue</a></html>'
        with patch("sources.polite_get", return_value=home), \
             patch("sources.polite_get_pdf", return_value="contact@example.ma"):
            records = sources.find_official_contact_records("https://example.ma/", max_pages=4)

        self.assertIn(
            {"email": "contact@example.ma", "source_url": "https://example.ma/documents/catalogue-2026.pdf"},
            records,
        )

    def test_index_query_variants_cover_transport_buyer_tender_types(self):
        query_text = " ".join(sources.MP_INDEX_QUERY_VARIANTS).lower()
        self.assertIn("transport du personnel", query_text)
        self.assertIn("mise à disposition des chauffeurs", query_text)
        self.assertIn("location de véhicules avec chauffeur", query_text)
        self.assertIn("barid al maghrib", query_text)
        self.assertIn("autoroutes du maroc", query_text)
        self.assertIn("transport de personnel", sources.MP_INDEX_EXTRA_KEYWORDS)


if __name__ == "__main__":
    unittest.main()
