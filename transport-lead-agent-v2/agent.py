import csv
import json
import os
import re
import smtplib
import imaplib
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage

import sources
from urllib.parse import quote_plus, unquote, urljoin, urlparse, urlencode
from urllib.request import Request, urlopen

OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(OUT_DIR, "contacted.json")
LEADS_JSON = os.path.join(OUT_DIR, "leads.json")
LEADS_CSV = os.path.join(OUT_DIR, "leads.csv")
TENDERS_JSON = os.path.join(OUT_DIR, "tenders.json")
TENDERS_CSV = os.path.join(OUT_DIR, "tenders.csv")
MAX_EMAILS = int(os.environ.get("MAX_EMAILS_PER_RUN", "10"))
MAX_EMAILS_PER_HOUR = int(os.environ.get("MAX_EMAILS_PER_HOUR", "20"))
# V2 safety default: preview only; workflow also omits SMTP credentials.
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}
V1_HISTORY_FILE = os.environ.get("V1_HISTORY_FILE", "").strip()
PRESENTATION_URL = os.environ.get("PRESENTATION_URL", "https://trmb.ma/").strip()
PRESENTATION_PDF_URL = os.environ.get(
    "PRESENTATION_PDF_URL",
    "https://trmb.ma/assets/documents/presentation-trmb-services.pdf?v=20261018",
).strip()
PRESENTATION_PDF_FILENAME = os.environ.get(
    "PRESENTATION_PDF_FILENAME", "Presentation_TRMB_SERVICES_2026.pdf"
).strip()
SMTP_RETRIES = int(os.environ.get("SMTP_RETRIES", "3"))
_PRESENTATION_PDF_CACHE = None
_PRESENTATION_PDF_ERROR = None

# Free sources only. No Tavily/API credits required.
# Queries focus on Moroccan companies likely to BUY transport/logistics,
# rather than searching only for pages explicitly saying "need transport".
# Each query carries a target-sector hint. The hint helps classify a potential buyer
# even if Bing's short RSS snippet omits the sector words.
QUERIES = [
    ('entreprises agroalimentaires Maroc fabricants site officiel contact', 'agroalimentaire industrie'),
    ('usines industrielles Maroc site officiel contact entreprise', 'industrie'),
    ('fabricants pièces automobiles Maroc entreprise contact site officiel', 'automobile industrie'),
    ('grossistes distributeurs Maroc entreprise site officiel contact', 'distribution grossiste'),
    ('laboratoires pharmaceutiques Maroc entreprise contact site officiel', 'pharmaceutique'),
    ('importateurs exportateurs Maroc entreprise site officiel contact', 'importateur exportateur'),
    ('entreprises BTP construction Maroc site officiel contact', 'construction chantier'),
    ('supermarchés commerce distribution Maroc entreprises contact', 'retail distribution'),
    ('hôtels groupes hôteliers Maroc site officiel contact', 'hôtel tourisme'),
    ('entrepôts plateformes industrielles Maroc entreprises contact', 'entrepôt logistique'),
    ('fabricants producteurs Maroc usine entreprise contact', 'fabrication production'),
    ('entreprises Maroc transport marchandises appel offres logistique', 'transport marchandises'),
    ('"demande de devis transport" Maroc entreprise', 'demande de devis transport'),
    ('"recherche prestataire transport" Maroc entreprise', 'recherche prestataire transport'),
    ('"appel d offres transport" Maroc entreprise logistique', 'appel d offres transport'),
    ('"consultation transport logistique" Maroc entreprise', 'consultation transport logistique'),
]

GENERIC_EMAIL_PREFIXES = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon"}
BAD_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com",
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
}
CONTACT_PATHS = (
    "contact", "contact-us", "nous-contacter", "contactez-nous",
    "mentions-legales", "mentions-légales", "a-propos", "about",
)
BUYER_SIGNALS = (
    "demande de devis", "demander un devis", "besoin de transport", "besoin transport",
    "recherche prestataire", "recherche un prestataire", "appel d'offre", "appel d offres",
    "sous-traitance", "transport quotidien", "navette", "livraison", "distribution",
    "affrètement", "chargeur", "location de véhicules", "véhicules avec chauffeur",
    "transport des salariés", "transport du personnel", "flux de marchandises",
    "approvisionnement", "expédition", "importation", "exportation",
)
DIRECT_INTENT_SIGNALS = (
    "demande de devis transport", "demande de cotation transport", "consultation transport",
    "recherche prestataire transport", "recherche un prestataire", "recherche transporteur",
    "recherche un transporteur", "appel d offres transport", "appel d'offres transport",
    "externalisation transport", "besoin de transport", "besoin transport",
    "contrat de transport", "marché transport", "marche transport", "marché logistique",
    "marche logistique", "transport quotidien", "livraison quotidienne",
)
PROVIDER_SIGNALS = (
    "société de transport", "societe de transport", "transporteur routier",
    "transporteur international", "entreprise de transport", "transport & logistique",
    "transport et logistique", "prestataire logistique", "freight forwarder",
    "commissionnaire de transport", "notre flotte", "nos camions", "semi-remorques",
    "camions 8x4", "services de transport", "transporteur de marchandises",
)
DIRECTORY_SIGNALS = (
    "annuaire", "liste des fournisseurs", "top fournisseurs", "comparatif",
    "magazine", "actualités", "actualites", "guide", "fiche fournisseur",
    "directory", "yellow pages", "fournisseurs transport maroc",
    "base de données b2b", "base de donnees b2b", "fournisseurs b2b",
    "plateforme des annonces des appels d'offres", "appels d'offres transport",
    "appels d'offres logistique", "appels d'offres au maroc",
    "appel d'offres transport", "market intelligence", "market research",
    "recherche ice", "ice maroc", "registre de commerce",
    "identifiant commun de l'entreprise", "verification entreprise",
    "vérification entreprise", "recherche d'entreprise", "recherche entreprise",
    "appel d'offre maroc", "appels d'offres publics", "appels d'offres en cours",
    "marchés publics", "marches publics", "tender platform",
)
TARGET_SECTOR_SIGNALS = (
    "usine", "industriel", "industrie", "agroalimentaire", "agricole", "distribution",
    "grossiste", "retail", "supermarché", "supermarche", "automobile", "pharmaceutique",
    "pharma", "construction", "chantier", "bâtiment", "batiment", "importateur",
    "exportateur", "fabrication", "production", "entrepôt", "entrepot", "zone industrielle",
    "plateforme", "commerce", "hôtel", "hotel", "tourisme", "laboratoire", "fabricant",
)

# Hard blocks for search results that are clearly financial content, media, or directories.
# These are never legitimate TRMB transport buyers, even if a broad search returns them.
NON_PROSPECT_DOMAINS = {
    "stockanalysis.com", "zacks.com", "marketbeat.com", "investing.com",
    "marketwatch.com", "seekingalpha.com", "macrotrends.net", "marketscreener.com",
    "tradingview.com", "barchart.com", "benzinga.com", "tipranks.com",
    "simplywall.st", "companiesmarketcap.com", "finance.yahoo.com",
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com", "wikipedia.org",
    "glassdoor.com", "kerix.net", "marocannuaire.org", "kompass.com",
    # Directories, tender aggregators, media/research pages, and transport competitors
    # observed in prior runs; do not treat these as transport buyers.
    "maroccontact.com", "wuripay.com", "lesoffres.ma", "industriemaroc.com",
    "fournilink.com", "datao.ma", "europages.fr", "yelo.ma", "procurement.ma",
    "mordorintelligence.com", "freshtrack.ma", "startupmedias.net", "bav.admin.ch",
    "comfortgateway.ma", "tilditours.ma", "msotransports.com", "innovaimport.ma",
    "sonix.ai", "scribd.com", "aomaroc.com", "bestwebsite.com",
    # Company lookup tools and tender aggregators are not themselves freight buyers.
    "icemorocco.com", "francemarches.com", "welipro.com",
}
FINANCE_NOISE = (
    "stock price", "stock price today", "stock price & overview", "stock market",
    "stock quote", "share price", "price target", "analyst rating", "analyst ratings",
    "marketbeat", "zacks", "stockanalysis", "earnings per share", "market capitalization",
    "nasdaq", "nyse", "ticker symbol", "investment research", "dividend history",
)
NON_PROSPECT_SIGNALS = (
    "audio transcription", "transcribe audio", "microsoft copilot", "speech-to-text",
    "speech to text", "transcription audio", "transcription d'audio",
)
MOROCCO_SIGNALS = (
    "maroc", "morocco", "casablanca", "rabat", "tanger", "tangier", "marrakech",
    "marrakesh", "agadir", "kenitra", "kénitra", "fes", "fès", "meknes", "meknès",
    "oujda", "tetouan", "tétouan", "mohammedia", "safi", "el jadida", "berrechid",
    "benguerir", "benguérir",
)


# Verified public business contacts taken from the companies' own contact pages.
# This deterministic free fallback keeps the agent useful when public search engines
# are blocked/rate-limited. Messages remain generic and do not claim a confirmed need.
SEEDED_PUBLIC_CONTACTS = [
    {
        "company": "Ciments du Maroc", "email": "commercial@cimar.co.ma",
        "domain": "cimentsdumaroc.com",
        "url": "https://www.cimentsdumaroc.com/fr/contacteznous",
        "email_source_url": "https://www.cimentsdumaroc.com/fr/contacteznous",
        "need": "transport de matériaux et logistique industrielle",
        "sector_hint": "matériaux de construction / industrie", "score": 82,
    },
    {
        "company": "Sothema", "email": "sothema@sothema.ma",
        "domain": "sothema.com", "url": "https://www.sothema.com/contact/",
        "email_source_url": "https://www.sothema.com/contact/",
        "need": "transport pour l'industrie pharmaceutique et distribution",
        "sector_hint": "industrie pharmaceutique", "score": 80,
    },
    {
        "company": "MAROC PET FOOD", "email": "contact@marocpetfood.ma",
        "domain": "marocpetfood.ma", "url": "https://marocpetfood.ma/index.html",
        "email_source_url": "https://marocpetfood.ma/index.html",
        "need": "transport de produits agroalimentaires et approvisionnement",
        "sector_hint": "agro-industrie / fabrication", "score": 78,
    },
    {
        "company": "Imany Food", "email": "contact@imanyfood.com",
        "domain": "imanyfood.com", "url": "https://www.imanyfood.com/mentions-legales/index.html",
        "email_source_url": "https://www.imanyfood.com/mentions-legales/index.html",
        "need": "transport de produits alimentaires et flux import-export",
        "sector_hint": "import-export / agroalimentaire", "score": 76,
    },
    {
        "company": "Al Halabi Food", "email": "contact@alhalabi.ma",
        "domain": "alhalabi.ma", "url": "https://www.alhalabi.ma/about/?lang=en",
        "email_source_url": "https://www.alhalabi.ma/about/?lang=en",
        "need": "transport et distribution de produits alimentaires",
        "sector_hint": "fabrication / agroalimentaire", "score": 76,
    },
    {
        "company": "Framaco", "email": "commercial@conserverieframaco.com",
        "domain": "conserverieframaco.com", "url": "https://www.conserverieframaco.com/en/contact/",
        "email_source_url": "https://www.conserverieframaco.com/en/contact/",
        "need": "transport de conserves alimentaires et distribution",
        "sector_hint": "industrie agroalimentaire", "score": 75,
    },
    {
        "company": "Conserves Nora", "email": "contact@norafood.com",
        "domain": "norafood.net", "url": "https://www.norafood.net/English/default.aspx",
        "email_source_url": "https://www.norafood.net/English/default.aspx",
        "need": "transport de produits agroalimentaires et expédition",
        "sector_hint": "fabrication / agroalimentaire", "score": 75,
    },
    {
        "company": "FinaAfric", "email": "contact@finaafric.com",
        "domain": "finaafric.com", "url": "https://www.finaafric.com/contact",
        "email_source_url": "https://www.finaafric.com/contact",
        "need": "transport industriel et approvisionnement",
        "sector_hint": "industrie / ingrédients alimentaires", "score": 74,
    },
    {
        "company": "Primasource Maroc", "email": "contact@primasourcemaroc.com",
        "domain": "primasourcemaroc.com", "url": "https://primasourcemaroc.com/contact-2/",
        "email_source_url": "https://primasourcemaroc.com/contact-2/",
        "need": "transport d'ingrédients agroalimentaires et approvisionnement",
        "sector_hint": "importation / distribution agroalimentaire", "score": 72,
    },
    {
        "company": "Oland Group", "email": "contact@olandgroup.ma",
        "domain": "olandgroup.ma", "url": "https://olandgroup.ma/a-propos/",
        "email_source_url": "https://olandgroup.ma/a-propos/",
        "need": "transport de produits laitiers et distribution agroalimentaire",
        "sector_hint": "fabrication agroalimentaire / production industrielle", "score": 80,
    },
    {
        "company": "Remora", "email": "m.filali@remora.ma",
        "domain": "remora.ma", "url": "https://remora.ma/contact/",
        "email_source_url": "https://remora.ma/contact/",
        "need": "transport industriel lié aux équipements et lignes de production agroalimentaire",
        "sector_hint": "fabrication industrielle / équipements de production", "score": 76,
    },
    {
        "company": "MTN Maroc", "email": "contact@mtn.co.ma",
        "domain": "mtn.co.ma", "url": "https://mtn.co.ma/contact/",
        "email_source_url": "https://mtn.co.ma/contact/",
        "need": "transport d'équipements et approvisionnement industriel",
        "sector_hint": "fabrication mécanique / maintenance industrielle", "score": 74,
    },
    {
        "company": "Industrial Manufacturing and Services Maroc", "email": "info@imsmaroc.ma",
        "domain": "imsmaroc.ma", "url": "https://imsmaroc.ma/contact",
        "email_source_url": "https://imsmaroc.ma/contact",
        "need": "transport d'équipements et flux de production industrielle",
        "sector_hint": "fabrication et services industriels", "score": 76,
    },
    {
        "company": "MACOBATE", "email": "contact@macobate.com",
        "domain": "macobate.ma", "url": "https://macobate.ma/fr/contact",
        "email_source_url": "https://macobate.ma/fr/contact",
        "need": "transport lié aux projets et équipements industriels",
        "sector_hint": "ingénierie industrielle / projets", "score": 75,
    },
    {
        "company": "APRO INDUSTRIE", "email": "contact@apro-industrie.ma",
        "domain": "apro-industrie.ma", "url": "https://apro-industrie.ma/contactez-nous/",
        "email_source_url": "https://apro-industrie.ma/contactez-nous/",
        "need": "transport de fournitures et équipements industriels",
        "sector_hint": "fournitures industrielles / B2B", "score": 72,
    },
    {
        "company": "Concept Industries", "email": "info@concept-industries.ma",
        "domain": "concept-industries.ma", "url": "https://concept-industries.ma/contact/",
        "email_source_url": "https://concept-industries.ma/contact/",
        "need": "transport d'équipements et approvisionnement industriel",
        "sector_hint": "industrie / fabrication", "score": 74,
    },
    {
        "company": "Comptoir Équipements Industriel Maroc", "email": "contact@cei.ma",
        "domain": "cei.ma", "url": "https://cei.ma/contact",
        "email_source_url": "https://cei.ma/contact",
        "need": "transport de matériel industriel, pièces BTP et équipements de manutention",
        "sector_hint": "distribution industrielle / BTP / manutention", "score": 75,
    },
    {
        "company": "Agrimatco Maroc", "email": "agrimatco.maroc@agrimatco-af.com",
        "domain": "agrimatco.ma", "url": "https://www.agrimatco.ma/contact/",
        "email_source_url": "https://www.agrimatco.ma/contact/",
        "need": "transport de fournitures agricoles et approvisionnement",
        "sector_hint": "importation / distribution agricole", "score": 74,
    },
    {
        "company": "MEGA SOURCINGINDUSTRY", "email": "commercial@megasourcingindustry.ma",
        "domain": "megaindustry.ma", "url": "https://www.megaindustry.ma/contact",
        "email_source_url": "https://www.megaindustry.ma/contact",
        "need": "transport de fournitures et équipements industriels importés",
        "sector_hint": "importation / distribution industrielle", "score": 74,
    },
    {
        "company": "DISLOG Group", "email": "contact@dislogroup.ma",
        "domain": "dislogroup.com", "url": "https://dislogroup.com/en/contact/",
        "email_source_url": "https://dislogroup.com/en/contact/",
        "need": "transport de produits de grande consommation et flux de distribution nationale",
        "sector_hint": "industrie / distribution B2B", "score": 82,
    },
    {
        "company": "Zalar Holding", "email": "info@zalar.ma",
        "domain": "zalar.ma", "url": "https://zalar.ma/fr/accueil/",
        "email_source_url": "https://zalar.ma/fr/accueil/",
        "need": "transport de produits avicoles, aliments pour animaux et approvisionnement industriel",
        "sector_hint": "agro-industrie / filière avicole", "score": 84,
    },
    {
        "company": "Maroc Ingrédients", "email": "info@marocingredients.com",
        "domain": "marocingredients.com", "url": "https://marocingredients.com/contact-2.html",
        "email_source_url": "https://marocingredients.com/contact-2.html",
        "need": "transport de matières premières industrielles et ingrédients alimentaires",
        "sector_hint": "import-export / matières premières", "score": 82,
    },
    {
        "company": "SIOF", "email": "info@siofgroup.com",
        "domain": "siofgroup.com", "url": "https://www.siofgroup.com/en",
        "email_source_url": "https://www.siofgroup.com/en",
        "need": "transport de produits agroalimentaires, huiles et approvisionnement",
        "sector_hint": "industrie agroalimentaire / production", "score": 82,
    },
    {
        "company": "Excelo – Best Biscuits Maroc", "email": "export@excelo.ma",
        "domain": "excelo.ma", "url": "https://excelo.ma/",
        "email_source_url": "https://excelo.ma/wp-content/uploads/2025/09/Catalogue_Excelo.pdf",
        "need": "transport de produits fabriqués et flux de distribution/export",
        "sector_hint": "fabrication agroalimentaire / biscuits", "score": 80,
    },
    {
        "company": "La SOMADIR", "email": "germa@somadir.com",
        "domain": "somadir.com", "url": "https://www.somadir.com/contact/",
        "email_source_url": "https://www.somadir.com/contact/",
        "need": "transport de produits industriels et approvisionnement entre sites",
        "sector_hint": "industrie agroalimentaire / production", "score": 80,
    },
    {
        "company": "Damandis", "email": "malika.ouchhal@damandis.ma",
        "domain": "damandis.ma", "url": "https://damandis.ma/contact",
        "email_source_url": "https://damandis.ma/contact",
        "need": "transport et distribution de produits FMCG entre plateformes et régions",
        "sector_hint": "distribution B2B / produits de grande consommation", "score": 78,
    },
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def configured_port(env_name, default):
    """Read a valid TCP port from environment, falling back when missing or blank."""
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        print(f"ℹ️ {env_name} is empty; using default port {default}")
        return default
    try:
        port = int(raw)
    except ValueError:
        print(f"⚠️ {env_name}={raw!r} is not a number; using default port {default}")
        return default
    if not 1 <= port <= 65535:
        print(f"⚠️ {env_name}={raw!r} is outside the valid port range; using {default}")
        return default
    return port


def http_get(url, timeout=20):
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TRMB-Services-Lead-Agent/3.0)",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read(700000).decode("utf-8", errors="ignore")


# Public SearXNG instances are free to query but can rate-limit or disable JSON.
# Try several; if unavailable, fall back to the other free search sources.
SEARXNG_INSTANCES = (
    "https://searx.ononoki.org",
    "https://search.jeremyh.xyz",
    "https://priv.au",
)


def searxng_search(q):
    for base in SEARXNG_INSTANCES:
        url = base.rstrip("/") + "/search?" + urlencode({
            "q": q, "format": "json", "language": "fr-FR",
            "categories": "general", "safesearch": 1,
        })
        try:
            payload = json.loads(http_get(url, timeout=15))
            results = []
            for item in payload.get("results", []):
                link = str(item.get("url", "")).strip()
                title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip()
                content = re.sub(r"\\s+", " ", str(item.get("content", ""))).strip()
                if link.startswith(("http://", "https://")) and title:
                    results.append({
                        "title": title, "url": link, "content": content,
                        "source": "searxng",
                    })
            if results:
                return results[:10]
        except Exception as e:
            print(f"⚠️ SearXNG instance unavailable ({base}): {e}")
    return []


def ddgs_search(q):
    """Search through the free DDGS metasearch library; no API key required."""
    try:
        from ddgs import DDGS
    except ImportError:
        print("⚠️ ddgs package not installed; continuing with fallback search")
        return []

    try:
        ddgs = DDGS(timeout=12)
        raw_results = ddgs.text(
            q,
            region="ma-fr",
            safesearch="moderate",
            max_results=10,
            backend="auto",
        )
        results = []
        for item in raw_results or []:
            link = str(item.get("href") or item.get("url") or "").strip()
            title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip()
            content = re.sub(r"\s+", " ", str(item.get("body") or item.get("content") or "")).strip()
            if link.startswith(("http://", "https://")) and title:
                results.append({
                    "title": title, "url": link, "content": content,
                    "source": "ddgs",
                })
        return results[:10]
    except Exception as e:
        print(f"⚠️ DDGS search unavailable: {e}")
        return []


def bing_rss_search(q):
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(q)
    raw = http_get(url, timeout=20)
    root = ET.fromstring(raw)
    results = []
    for item in root.findall(".//item"):
        title = re.sub(r"\s+", " ", item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        if link:
            results.append({"title": title, "url": link, "content": desc, "source": "bing-rss"})
    return results[:10]


def ddg_html_search(q):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(q)
    raw = http_get(url, timeout=20)
    results = []
    pattern = r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
    for m in re.finditer(pattern, raw, flags=re.I | re.S):
        link = unquote(m.group(1))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        if link:
            results.append({"title": title, "url": link, "content": "", "source": "duckduckgo"})
    return results[:10]


def search_web(q, require_morocco=True):
    # Prefer the free meta-search API; it combines available engines rather than
    # relying on a single RSS endpoint that has returned unrelated results.
    engines = (
        # DDGS rotates across public search backends and is less brittle than scraping
        # one search engine's HTML directly. No key or paid search credits required.
        ("ddgs", ddgs_search),
        ("bing-rss", bing_rss_search),
        ("duckduckgo", ddg_html_search),
        ("searxng", searxng_search),
    )
    for source, search_fn in engines:
        try:
            results = search_fn(q)
            if not results:
                continue
            if not require_morocco:
                return results, source
            matched = [
                item for item in results
                if is_morocco_candidate(item.get("title", ""), item.get("content", ""), item.get("url", ""))
                and not is_non_prospect_source(item.get("title", ""), item.get("content", ""), item.get("url", ""))
            ]
            if matched:
                # Return the filtered set to keep downstream crawling focused.
                return matched, source
            print(f"⚠️ {source} returned no Morocco-matched results; trying next source")
        except Exception as e:
            print(f"⚠️ {source} unavailable: {e}")
    return [], "none"


def strip_html(raw):
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_page(url):
    # All company-site crawling goes through sources.polite_get so robots.txt,
    # per-host delay, and retry handling are respected.
    if not url or not url.startswith(("http://", "https://")):
        return "", []
    try:
        raw = sources.polite_get(url)
        if not raw:
            return "", []
        links = re.findall(r'href=["\']([^"\']+)["\']', raw, flags=re.I)
        return strip_html(raw)[:120000], links
    except Exception as exc:
        print(f"⚠️ Polite page fetch failed: {url} ({exc})")
        return "", []


def domain(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def extract_emails(text):
    found = []
    for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or ""):
        email = email.lower().strip(".,;:()[]<>")
        if email.split("@", 1)[0] not in GENERIC_EMAIL_PREFIXES and email not in found:
            found.append(email)
    return found


def usable_email(email, url=""):
    if not email or "@" not in email:
        return False
    local, host = email.rsplit("@", 1)
    if len(local) < 2 or "." not in host:
        return False
    host = host.lower()
    page_domain = domain(url)
    if host in BAD_DOMAINS:
        return False
    return not page_domain or host == page_domain or host.endswith("." + page_domain) or page_domain.endswith("." + host)


def discover_email_record(url):
    """Return the email and exact page URL from the official company site."""
    try:
        candidates = sources.find_official_contact_records(url, max_pages=4)
    except Exception as exc:
        print(f"⚠️ Official contact discovery failed for {url}: {exc}")
        candidates = []
    for item in candidates:
        email = str(item.get("email", "")).strip().lower()
        source_url = str(item.get("source_url", "")).strip()
        if usable_email(email, url) and source_url and domain(source_url) == domain(url):
            return email, source_url
    return "", ""


def discover_email(url):
    """Backward-compatible email-only helper."""
    return discover_email_record(url)[0]


def public_email(title, url):
    """Find a public business mailbox only when the evidence page is on the official host."""
    host = domain(url)
    if not host:
        return "", ""
    queries = [
        f'site:{host} "@" contact',
        f'site:{host} email',
        f'"{title}" Maroc site:{host} contact email',
    ]
    for q in queries:
        results, _ = search_web(q, require_morocco=False)
        for item in results:
            source_url = str(item.get("url", "")).strip()
            # Do not extract contact details from directories or third-party snippets.
            if not source_url or domain(source_url) != host:
                continue
            blob = f"{item.get('title', '')} {item.get('content', '')}"
            for email in extract_emails(blob):
                prefix = email.split("@", 1)[0].lower()
                if prefix not in sources.GENERIC_PREFIXES:
                    continue
                if usable_email(email, url):
                    return email, source_url
    return "", ""


def clean_company(title):
    t = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(r"\s*[|–—-]\s*(Accueil|Home|Contact|Site officiel|Official).*?$", "", t, flags=re.I)
    return t[:100] or "votre entreprise"


def has_direct_intent(text):
    t = (text or "").lower().replace("’", "'")
    return any(signal.replace("’", "'") in t for signal in DIRECT_INTENT_SIGNALS)


def prospect_priority(text, value):
    if has_direct_intent(text):
        return "HOT"
    if value >= 60:
        return "WARM"
    return "STANDARD"


def score(text):
    t = (text or "").lower().replace("’", "'")
    buyer = sum(15 for x in BUYER_SIGNALS if x in t)
    sector = sum(20 for x in TARGET_SECTOR_SIGNALS if x in t)
    intent = sum(30 for x in DIRECT_INTENT_SIGNALS if x in t)
    provider = sum(30 for x in PROVIDER_SIGNALS if x in t)
    directory = sum(60 for x in DIRECTORY_SIGNALS if x in t)
    # Direct statements of transport/logistics intent receive stronger priority.
    return max(0, min(100, buyer + sector + intent - provider - directory))


def is_bad_prospect(title, content, url):
    # Reject obvious transport/logistics providers from the result title immediately.
    # V2 is intended to find companies that BUY transport, not competitors selling it.
    title_text = re.sub(r"\\s+", " ", title or "").lower().replace("’", "'")
    provider_title_signals = (
        "société de transport", "societe de transport",
        "société de transports", "societe de transports",
        "transporteur routier", "transporteur international",
        "entreprise de transport", "transport et logistique",
        "transport & logistique", "transport/logistique",
        "freight forwarder", "commissionnaire de transport",
        "transport de marchandises", "transport international",
        "transport routier", "nos camions", "notre flotte",
    )
    if any(signal in title_text for signal in provider_title_signals):
        return True

    t = f"{title} {content} {url}".lower().replace("’", "'")
    if any(x.replace("’", "'") in t for x in DIRECTORY_SIGNALS):
        return True
    provider_hits = sum(1 for x in PROVIDER_SIGNALS if x.replace("’", "'") in t)
    buyer_hits = sum(1 for x in BUYER_SIGNALS if x.replace("’", "'") in t)
    sector_hits = sum(1 for x in TARGET_SECTOR_SIGNALS if x in t)
    return provider_hits >= 2 and buyer_hits <= 2 and sector_hits < 2


def is_non_prospect_source(title, content, url):
    host = domain(url)
    # Government portals/regulators need a formal procurement context; do not
    # treat public-authority contact pages as ordinary commercial prospects.
    if host.endswith(".gov.ma") or host == "gov.ma":
        return True
    if host in NON_PROSPECT_DOMAINS or any(host.endswith("." + x) for x in NON_PROSPECT_DOMAINS):
        return True
    text = f"{title} {content} {url}".lower()
    return (
        any(signal in text for signal in FINANCE_NOISE)
        or any(signal in text for signal in NON_PROSPECT_SIGNALS)
    )


def is_morocco_candidate(title, content, url):
    host = domain(url)
    if host.endswith(".ma"):
        return True
    text = f"{title} {content} {url}".lower()
    return any(signal in text for signal in MOROCCO_SIGNALS)


def has_real_sector_evidence(text):
    t = (text or "").lower()
    return any(x in t for x in TARGET_SECTOR_SIGNALS) or any(x in t for x in BUYER_SIGNALS)


def detect_need(content):
    t = (content or "").lower()
    if any(x in t for x in ("transport du personnel", "transport des salariés", "navette", "transport personnel")):
        return "transport de personnel / navettes"
    if any(x in t for x in ("livraison", "distribution", "approvisionnement")):
        return "livraison / distribution"
    if any(x in t for x in ("affrètement", "chargeur", "flux de marchandises")):
        return "affrètement / flux de marchandises"
    if any(x in t for x in ("importation", "exportation", "expédition")):
        return "transport lié aux flux import/export"
    return "transport et logistique"


def build_message(company, need):
    subject = "Proposition transport & logistique – TRMB SERVICES"
    body = (
        "Bonjour,\n\n"
        "Je me permets de vous contacter au nom de TRMB SERVICES, société spécialisée dans les solutions de transport et de logistique au Maroc.\n\n"
        f"Nous accompagnons les entreprises pour leurs besoins de {need}, avec des solutions adaptées aux trajets, volumes, fréquences et contraintes opérationnelles.\n\n"
        f"Nous contactons {company} pour vous présenter nos solutions et vérifier si elles peuvent répondre à vos besoins actuels ou futurs. Nous serions ravis d'échanger avec votre équipe et de vous proposer une offre adaptée si cela vous intéresse.\n\n"
        "Vous trouverez notre présentation commerciale TRMB SERVICES en PDF en pièce jointe.\n\n"
        f"Site web : {PRESENTATION_URL}\n\n"
        "Si vous avez un besoin actuel ou à venir, envoyez-nous simplement les informations disponibles. Nous vous répondrons rapidement.\n\n"
        "Bien cordialement,\n"
        "TRMB SERVICES\n"
        f"{PRESENTATION_URL}"
    )
    return subject, body


def load_history():
    """Merge this V2 history with the latest V1 suppression history."""
    data = {}
    for path in (HISTORY_FILE, V1_HISTORY_FILE):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                part = json.load(f)
            if isinstance(part, dict):
                # Preserve V2-specific records if keys overlap.
                data = {**part, **data}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
    return data


def build_contact_suppression(history):
    """Normalize old email, URL and domain records to prevent duplicate outreach."""
    identifiers = set()
    domains = set()

    def register(value):
        if value is None:
            return
        raw = str(value).strip().lower()
        if not raw:
            return
        identifiers.add(raw)
        clean = raw.removeprefix("mailto:")
        if "@" in clean and not clean.startswith(("http://", "https://")):
            host = clean.rsplit("@", 1)[1].split("/", 1)[0].strip(".")
            if host and "." in host:
                domains.add(host.removeprefix("www."))
            return
        try:
            candidate = clean if clean.startswith(("http://", "https://")) else "//" + clean
            host = (urlparse(candidate).hostname or "").lower().removeprefix("www.")
            if "." in host:
                domains.add(host)
        except Exception:
            return

    for key, value in history.items():
        register(key)
        if isinstance(value, dict):
            for field in ("email", "domain", "url", "website"):
                register(value.get(field))
    return identifiers, domains


def was_contacted(email, website_domain, identifiers, known_domains):
    """Return true when this email or the same normalized company domain was contacted."""
    target = str(website_domain or "").strip().lower().removeprefix("www.")
    if "://" in target:
        target = domain(target)
    target = target.rstrip(".")
    address = str(email or "").strip().lower()
    if address and address in identifiers:
        return True
    if not target:
        return False
    for old_domain in known_domains:
        old_domain = str(old_domain).strip().lower().removeprefix("www.").rstrip(".")
        if not old_domain:
            continue
        if target == old_domain or target.endswith("." + old_domain) or old_domain.endswith("." + target):
            return True
    return False


def count_sends_in_last_hour(history, now=None):
    """Count successful SMTP submissions recorded in the rolling last hour."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - 3600
    count = 0
    for value in history.values():
        if not isinstance(value, dict):
            continue
        sent_at = value.get("sent_at")
        if not sent_at:
            continue
        try:
            parsed = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed.astimezone(timezone.utc).timestamp() >= cutoff:
                count += 1
        except (TypeError, ValueError, OverflowError):
            continue
    return count


def save_history(data):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_to_sent_folder(msg):
    """Append a copy of an SMTP-submitted message to the account's IMAP Sent folder."""
    host = os.environ.get("IMAP_HOST", "").strip() or os.environ.get("SMTP_HOST", "").strip()
    port = configured_port("IMAP_PORT", 993)
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    explicit_folder = os.environ.get("IMAP_SENT_FOLDER", "").strip()

    if not all([host, user, password]):
        return False, "IMAP credentials not configured"

    client = None
    try:
        context = ssl.create_default_context()
        if port == 993:
            client = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=25)
        else:
            client = imaplib.IMAP4(host, port, timeout=25)
            if port == 143:
                client.starttls(ssl_context=context)

        client.login(user, password)

        folder = explicit_folder
        if not folder:
            status, rows = client.list()
            if status != "OK":
                return False, "IMAP could not list mail folders"
            folders = []
            for row in rows or []:
                if not row:
                    continue
                decoded = row.decode("utf-8", errors="ignore")
                match = re.search(r'\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)$', decoded)
                if not match:
                    continue
                flags = match.group(1).lower()
                raw_name = match.group(2).strip()
                if raw_name.startswith('"') and raw_name.endswith('"'):
                    raw_name = raw_name[1:-1].replace(r'\"', '"')
                folders.append((raw_name, flags))

            for name, flags in folders:
                if r"\sent" in flags:
                    folder = name
                    break
            if not folder:
                preferred = ("sent", "inbox.sent", "sent messages", "sent items", "envoyés", "envoyÃ©s")
                for name, _flags in folders:
                    if name.lower() in preferred:
                        folder = name
                        break
            if not folder:
                return False, "Could not identify Sent folder; set IMAP_SENT_FOLDER GitHub secret"

        status, response = client.append(
            folder,
            r"\Seen",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
        if status != "OK":
            return False, f"IMAP APPEND failed: {response}"
        return True, folder
    except Exception as e:
        return False, str(e)
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def load_presentation_pdf():
    """Download and validate the official TRMB Services presentation PDF once per run."""
    global _PRESENTATION_PDF_CACHE, _PRESENTATION_PDF_ERROR
    if _PRESENTATION_PDF_CACHE is not None:
        return _PRESENTATION_PDF_CACHE, ""
    if _PRESENTATION_PDF_ERROR is not None:
        return None, _PRESENTATION_PDF_ERROR

    if not PRESENTATION_PDF_URL.startswith(("https://", "http://")):
        _PRESENTATION_PDF_ERROR = "PRESENTATION_PDF_URL must be an HTTP(S) URL"
        return None, _PRESENTATION_PDF_ERROR

    req = Request(
        PRESENTATION_PDF_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TRMB-Services-Lead-Agent/3.0)",
            "Accept": "application/pdf,*/*;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=25) as response:
            data = response.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("presentation PDF exceeds the 10 MB attachment limit")
        if not data.startswith(b"%PDF-"):
            raise ValueError("downloaded presentation is not a valid PDF file")
        _PRESENTATION_PDF_CACHE = data
        print(f"📎 Official presentation PDF ready ({len(data)} bytes)")
        return data, ""
    except Exception as e:
        _PRESENTATION_PDF_ERROR = str(e)
        print(f"❌ Presentation PDF unavailable; no email will be sent without it: {_PRESENTATION_PDF_ERROR}")
        return None, _PRESENTATION_PDF_ERROR


def save_to_sent_folder(msg):
    """Try to save a copy into the mailbox's IMAP Sent folder; SMTP success is independent."""
    host = os.environ.get("IMAP_HOST", "").strip() or os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("IMAP_PORT", "993"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    explicit_folder = os.environ.get("IMAP_SENT_FOLDER", "").strip()
    if not all([host, user, password]):
        return False, "IMAP credentials not configured"

    client = None
    try:
        context = ssl.create_default_context()
        if port == 993:
            client = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=25)
        else:
            client = imaplib.IMAP4(host, port, timeout=25)
            if port == 143:
                client.starttls(ssl_context=context)
        client.login(user, password)

        folder = explicit_folder
        if not folder:
            status, rows = client.list()
            if status != "OK":
                return False, "IMAP could not list mailbox folders"
            folders = []
            for row in rows or []:
                if not row:
                    continue
                decoded = row.decode("utf-8", errors="ignore")
                match = re.search(r'\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)$', decoded)
                if not match:
                    continue
                flags = match.group(1).lower()
                raw_name = match.group(2).strip()
                if raw_name.startswith('"') and raw_name.endswith('"'):
                    raw_name = raw_name[1:-1].replace(r'\"', '"')
                folders.append((raw_name, flags))

            for name, flags in folders:
                if r"\sent" in flags:
                    folder = name
                    break
            if not folder:
                preferred = ("sent", "inbox.sent", "sent messages", "sent items", "envoyés", "envoyÃ©s")
                for name, _flags in folders:
                    if name.lower() in preferred:
                        folder = name
                        break
            if not folder:
                return False, "Sent folder not identified; set IMAP_SENT_FOLDER secret"

        status, response = client.append(
            folder,
            r"\Seen",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
        if status != "OK":
            return False, f"IMAP APPEND failed: {response}"
        return True, folder
    except Exception as e:
        return False, str(e)
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = configured_port("SMTP_PORT", 465)
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user
    if not all([host, user, password, sender]):
        return False, "SMTP credentials not configured"

    pdf_bytes, pdf_error = load_presentation_pdf()
    if pdf_bytes is None:
        return False, f"Presentation PDF unavailable; message not sent: {pdf_error}"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=PRESENTATION_PDF_FILENAME,
    )

    for attempt in range(1, SMTP_RETRIES + 1):
        try:
            if port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
                    smtp.login(user, password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=30) as smtp:
                    smtp.ehlo()
                    if port in (587, 25):
                        smtp.starttls(context=ssl.create_default_context())
                        smtp.ehlo()
                    smtp.login(user, password)
                    smtp.send_message(msg)

            saved, folder_or_error = save_to_sent_folder(msg)
            if saved:
                print(f"📨 Copy with PDF saved to IMAP Sent folder: {folder_or_error}")
            else:
                print(f"⚠️ Email accepted by SMTP, but Sent copy not saved: {folder_or_error}")
            return True, "accepted_by_smtp"
        except Exception as e:
            if attempt < SMTP_RETRIES:
                time.sleep(2 * attempt)
            else:
                return False, str(e)
    return False, "unknown SMTP error"


def save_tender_files(items):
    """Save public procurement opportunities separately from commercial buyer leads."""
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(TENDERS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    fields = ["retrieved_at", "title", "buyer", "deadline", "status", "keyword", "url", "source"]
    with open(TENDERS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(items)
    print(f"📑 Saved {len(items)} public tender opportunities separately from sales prospects.")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history()
    contacted, contacted_domains = build_contact_suppression(history)

    all_leads = []
    seen = set()
    source_counts = {}
    print("Starting Morocco-only B2B buyer discovery with free search sources...")

    # Public tenders are opportunities for TRMB to review/bid, not contacts to
    # email automatically as sales prospects. Keep their inventory in separate files.
    try:
        tender_opportunities = sources.find_tenders()
    except Exception as exc:
        print(f"⚠️ Official tender discovery failed safely: {exc}")
        tender_opportunities = []
    save_tender_files(tender_opportunities)

    for q, sector_hint in QUERIES:
        print(f"Searching: {q}")
        results, source = search_web(q)
        source_counts[source] = source_counts.get(source, 0) + 1
        for item in results:
            url = item.get("url", "").strip()
            title = item.get("title", "").strip()
            content = item.get("content", "").strip()
            if not url or not title:
                continue
            d = domain(url)
            key = d or url
            if key in seen or was_contacted('', d, contacted, contacted_domains):
                continue

            # Search-engine RSS can return unrelated global results for Morocco queries.
            # Filter known finance/media domains before visiting or contacting them.
            if is_non_prospect_source(title, content, url):
                print(f"⏭️ Rejected non-prospect/finance result: {title[:90]}")
                continue
            if not is_morocco_candidate(title, content, url):
                print(f"⏭️ Rejected non-Morocco result: {title[:90]}")
                continue

            # Confirm actual page evidence; never use the query's sector hint as proof.
            site_text, _ = fetch_page(url)
            evidence = f"{title} {content} {url} {site_text[:40000]}"
            if is_non_prospect_source(title, evidence, url):
                print(f"⏭️ Rejected finance/media page: {title[:90]}")
                continue
            if not is_morocco_candidate(title, evidence, url):
                print(f"⏭️ Rejected page without Morocco evidence: {title[:90]}")
                continue
            if not has_real_sector_evidence(evidence):
                print(f"⏭️ Rejected page without sector evidence: {title[:90]}")
                continue
            if is_bad_prospect(title, evidence, url):
                print(f"⏭️ Rejected transport provider/directory: {title[:90]}")
                continue

            s = score(evidence)
            if s < 15:
                continue
            seen.add(key)
            company = clean_company(title)
            email, email_source_url = discover_email_record(url)
            if not email:
                email, email_source_url = public_email(company, url)
            lead = {
                "timestamp": now_iso(),
                "company": company,
                "email": email,
                "domain": d,
                "url": url,
                "need": detect_need(evidence),
                "sector_hint": sector_hint,
                "score": s,
                "direct_intent": has_direct_intent(evidence),
                "priority": prospect_priority(evidence, s),
                "source": source,
                "email_source_url": email_source_url,
                "status": "qualified" if email else "no_email",
            }
            all_leads.append(lead)
            if len(all_leads) >= 30:
                break
        if len(all_leads) >= 30:
            break


    # Add independently researched contacts published on official company pages.
    # Historical URL/email/domain suppression is preserved, and search hits without
    # an email are enriched rather than duplicated when a verified contact exists.
    seeded_added = 0
    seeded_enriched = 0
    existing_by_domain = {
        str(item.get("domain", "")).lower().removeprefix("www."): item
        for item in all_leads if item.get("domain")
    }
    existing_emails = {str(item.get("email", "")).lower() for item in all_leads if item.get("email")}
    for seed in SEEDED_PUBLIC_CONTACTS:
        email = str(seed.get("email", "")).strip().lower()
        d = str(seed.get("domain", "")).strip().lower().removeprefix("www.")
        if not email or not d or was_contacted(email, d, contacted, contacted_domains):
            continue
        if email in existing_emails:
            continue

        existing = existing_by_domain.get(d)
        if existing and existing.get("email"):
            continue
        if existing:
            existing.update({
                "company": seed["company"],
                "email": email,
                "domain": d,
                "url": seed["url"],
                "email_source_url": seed.get("email_source_url", seed["url"]),
                "need": seed["need"],
                "sector_hint": seed["sector_hint"],
                "score": max(int(existing.get("score", 0) or 0), int(seed["score"])),
                "direct_intent": bool(existing.get("direct_intent", False)),
                "priority": "WARM" if max(int(existing.get("score", 0) or 0), int(seed["score"])) >= 60 else "STANDARD",
                "source": "verified-official-contact-fallback",
                "status": "qualified",
            })
            existing_emails.add(email)
            seeded_enriched += 1
            continue

        lead = {
            "timestamp": now_iso(),
            **seed,
            "direct_intent": False,
            "priority": "WARM" if seed["score"] >= 60 else "STANDARD",
            "source": "verified-official-contact-fallback",
            "email_source_url": seed.get("email_source_url", seed["url"]),
            "status": "qualified",
        }
        all_leads.append(lead)
        existing_by_domain[d] = lead
        existing_emails.add(email)
        seeded_added += 1
    print(
        f"Added {seeded_added} new official-contact fallback prospects; "
        f"enriched {seeded_enriched} search results with verified official contacts"
    )

    all_leads.sort(key=lambda x: (x.get("priority") == "HOT", x["score"], bool(x["email"])), reverse=True)
    qualified = [x for x in all_leads if x["email"] and x["score"] >= 20]
    sent = 0
    attempted = 0
    planned = 0
    smtp_failures = 0
    print(f"Mode V2: DRY_RUN={DRY_RUN} (true = preview only; no email sent)")
    sent_last_hour = count_sends_in_last_hour(history)
    if sent_last_hour >= MAX_EMAILS_PER_HOUR:
        print(
            f"⏸️ Hourly safety limit already reached "
            f"({sent_last_hour}/{MAX_EMAILS_PER_HOUR}); no emails will be sent this run"
        )

    for lead in qualified:
        # The per-run cap applies to attempts as well as successes. Otherwise a bad
        # SMTP configuration could try every qualified address despite a low cap.
        if attempted + planned >= MAX_EMAILS:
            print(f"⏸️ Per-run preview/attempt limit reached ({attempted + planned}/{MAX_EMAILS})")
            break
        if sent_last_hour + planned >= MAX_EMAILS_PER_HOUR:
            print(
                f"⏸️ Hourly safety limit reached "
                f"({sent_last_hour + planned}/{MAX_EMAILS_PER_HOUR}); remaining leads kept for a later run"
            )
            break
        email = lead["email"].lower()
        if was_contacted(email, lead.get("domain", ""), contacted, contacted_domains):
            continue
        subject, body = build_message(lead["company"], lead["need"])
        if DRY_RUN:
            planned += 1
            lead["status"] = "dry_run_preview"
            lead["preview_subject"] = subject
            lead["preview_note"] = "Preview only; not sent. Activate sending only after V2 review."
            print(f"🧪 DRY RUN — would email: {lead['company']} <{email}> | priority={lead.get('priority', 'STANDARD')} | direct_intent={lead.get('direct_intent', False)}")
            continue
        attempted += 1
        ok, detail = send_email(email, subject, body)
        if ok:
            sent += 1
            lead["status"] = "sent"
            lead["sent_at"] = now_iso()
            history[email] = {
                "company": lead["company"],
                "domain": lead["domain"],
                "sent_at": lead["sent_at"],
                "subject": subject,
            }
            contacted.add(email)
            contacted_domains.add(email.rsplit("@", 1)[1].lower())
            sent_last_hour += 1
            if lead["domain"]:
                contacted.add(lead["domain"].lower())
                contacted_domains.add(lead["domain"].lower().removeprefix("www."))
            print(f"✅ Email accepted by SMTP: {lead['company']} <{email}>")
        else:
            smtp_failures += 1
            lead["status"] = "smtp_error"
            lead["smtp_error"] = detail
            print(f"⚠️ SMTP failed for {email}: {detail}")

    with open(LEADS_JSON, "w", encoding="utf-8") as f:
        json.dump(all_leads, f, ensure_ascii=False, indent=2)

    fields = ["timestamp", "company", "email", "domain", "url", "need", "sector_hint", "score", "direct_intent", "priority", "source", "email_source_url", "status", "sent_at", "smtp_error", "preview_subject", "preview_note"]
    with open(LEADS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_leads)

    save_history(history)
    print(
        f"Searched {len(QUERIES)} queries ({', '.join(f'{k}={v}' for k, v in source_counts.items())}); "
        f"Found {len(all_leads)} buyer leads; qualified {len(qualified)}; previewed {planned}; attempts {attempted}; "
        f"sent {sent} emails; SMTP failures {smtp_failures}; "
        f"no email {sum(1 for x in all_leads if not x.get('email'))}"
    )


if __name__ == "__main__":
    main()
