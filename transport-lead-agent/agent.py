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
from urllib.parse import quote_plus, unquote, urljoin, urlparse, urlencode
from urllib.request import Request, urlopen

OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(OUT_DIR, "contacted.json")
LEADS_JSON = os.path.join(OUT_DIR, "leads.json")
LEADS_CSV = os.path.join(OUT_DIR, "leads.csv")
MAX_EMAILS = int(os.environ.get("MAX_EMAILS_PER_RUN", "3"))
MAX_EMAILS_PER_HOUR = int(os.environ.get("MAX_EMAILS_PER_HOUR", "3"))
PRESENTATION_URL = os.environ.get("PRESENTATION_URL", "https://trmb.ma/").strip()
SMTP_RETRIES = int(os.environ.get("SMTP_RETRIES", "3"))

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
}
FINANCE_NOISE = (
    "stock price", "stock price today", "stock price & overview", "stock market",
    "stock quote", "share price", "price target", "analyst rating", "analyst ratings",
    "marketbeat", "zacks", "stockanalysis", "earnings per share", "market capitalization",
    "nasdaq", "nyse", "ticker symbol", "investment research", "dividend history",
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
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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
    if not url or not url.startswith(("http://", "https://")):
        return "", []
    try:
        raw = http_get(url, timeout=12)
        links = re.findall(r'href=["\']([^"\']+)["\']', raw, flags=re.I)
        return strip_html(raw)[:120000], links
    except Exception:
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


def discover_email(url):
    text, links = fetch_page(url)
    if not text and not links:
        return ""
    candidates = extract_emails(text)
    for link in links:
        if link.lower().startswith("mailto:"):
            candidates.extend(extract_emails(unquote(link[7:].split("?", 1)[0])))
    checked = set()
    for link in links:
        absolute = urljoin(url, link)
        path = urlparse(absolute).path.lower().strip("/")
        if path in CONTACT_PATHS or any(path.endswith("/" + x) for x in CONTACT_PATHS):
            if absolute in checked:
                continue
            checked.add(absolute)
            page_text, _ = fetch_page(absolute)
            candidates.extend(extract_emails(page_text))
            if len(checked) >= 3:
                break
    for email in candidates:
        if usable_email(email, url):
            return email
    return ""


def public_email(title, url):
    host = domain(url)
    queries = []
    if host:
        queries.append(f'site:{host} "@" contact')
        queries.append(f'site:{host} email')
    queries.append(f'"{title}" Maroc contact email')
    for q in queries:
        results, _ = search_web(q, require_morocco=False)
        for item in results:
            candidate_url = item.get("url", "") or url
            blob = f"{item.get('title', '')} {item.get('content', '')}"
            for email in extract_emails(blob):
                # Keep public-email search constrained to the original company domain.
                if usable_email(email, url):
                    return email
    return ""


def clean_company(title):
    t = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(r"\s*[|–—-]\s*(Accueil|Home|Contact|Site officiel|Official).*?$", "", t, flags=re.I)
    return t[:100] or "votre entreprise"


def score(text):
    t = (text or "").lower()
    buyer = sum(15 for x in BUYER_SIGNALS if x in t)
    sector = sum(20 for x in TARGET_SECTOR_SIGNALS if x in t)
    provider = sum(30 for x in PROVIDER_SIGNALS if x in t)
    directory = sum(60 for x in DIRECTORY_SIGNALS if x in t)
    # For outbound prospecting, verified sector fit is sufficient; a company
    # need not publish a literal request for transport on its website.
    return max(0, min(100, buyer + sector - provider - directory))


def is_bad_prospect(title, content, url):
    t = f"{title} {content} {url}".lower()
    if any(x in t for x in DIRECTORY_SIGNALS):
        return True
    provider_hits = sum(1 for x in PROVIDER_SIGNALS if x in t)
    buyer_hits = sum(1 for x in BUYER_SIGNALS if x in t)
    sector_hits = sum(1 for x in TARGET_SECTOR_SIGNALS if x in t)
    return provider_hits >= 2 and buyer_hits <= 2 and sector_hits < 2


def is_non_prospect_source(title, content, url):
    host = domain(url)
    if host in NON_PROSPECT_DOMAINS or any(host.endswith("." + x) for x in NON_PROSPECT_DOMAINS):
        return True
    text = f"{title} {content} {url}".lower()
    return any(signal in text for signal in FINANCE_NOISE)


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
        f"Présentation : {PRESENTATION_URL}\n\n"
        "Si vous avez un besoin actuel ou à venir, envoyez-nous simplement les informations disponibles. Nous vous répondrons rapidement.\n\n"
        "Bien cordialement,\n"
        "TRMB SERVICES\n"
        f"{PRESENTATION_URL}"
    )
    return subject, body


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


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
                return False, "IMAP could not list mail folders"
            folders = []
            for row in rows or []:
                if not row:
                    continue
                decoded = row.decode("utf-8", errors="ignore")
                match = re.search(r'\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history()
    contacted = {str(k).lower() for k in history.keys()}
    for value in history.values():
        if isinstance(value, dict):
            for field in ("email", "domain"):
                if value.get(field):
                    contacted.add(str(value[field]).lower())

    all_leads = []
    seen = set()
    source_counts = {}
    print("Starting Morocco-only B2B buyer discovery with free search sources...")

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
            if key in seen or key in contacted:
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
            email = discover_email(url)
            if not email:
                email = public_email(company, url)
            lead = {
                "timestamp": now_iso(),
                "company": company,
                "email": email,
                "domain": d,
                "url": url,
                "need": detect_need(evidence),
                "sector_hint": sector_hint,
                "score": s,
                "source": source,
                "status": "qualified" if email else "no_email",
            }
            all_leads.append(lead)
            if len(all_leads) >= 30:
                break
        if len(all_leads) >= 30:
            break


    # Add verified official-contact fallbacks when search engines return little or no data.
    # Skip every company or email already contacted; never re-send on later scheduled runs.
    seeded_added = 0
    existing_domains_with_email = {
        x.get("domain", "").lower() for x in all_leads if x.get("email")
    }
    existing_emails = {x.get("email", "").lower() for x in all_leads if x.get("email")}
    for seed in SEEDED_PUBLIC_CONTACTS:
        email = seed["email"].lower()
        d = seed["domain"].lower()
        if email in contacted or d in contacted or email in existing_emails or d in existing_domains_with_email:
            continue
        lead = {
            "timestamp": now_iso(),
            **seed,
            "source": "verified-official-contact-fallback",
            "status": "qualified",
        }
        all_leads.append(lead)
        existing_emails.add(email)
        existing_domains_with_email.add(d)
        seeded_added += 1
    print(f"Added {seeded_added} verified official-contact fallback prospects")

    all_leads.sort(key=lambda x: (x["score"], bool(x["email"])), reverse=True)
    qualified = [x for x in all_leads if x["email"] and x["score"] >= 20]
    sent = 0
    smtp_failures = 0
    sent_last_hour = count_sends_in_last_hour(history)
    if sent_last_hour >= MAX_EMAILS_PER_HOUR:
        print(
            f"⏸️ Hourly safety limit already reached "
            f"({sent_last_hour}/{MAX_EMAILS_PER_HOUR}); no emails will be sent this run"
        )

    for lead in qualified:
        if sent >= MAX_EMAILS:
            break
        if sent_last_hour >= MAX_EMAILS_PER_HOUR:
            print(
                f"⏸️ Hourly safety limit reached "
                f"({sent_last_hour}/{MAX_EMAILS_PER_HOUR}); remaining leads kept for a later run"
            )
            break
        email = lead["email"].lower()
        if email in contacted or lead["domain"] in contacted:
            continue
        subject, body = build_message(lead["company"], lead["need"])
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
            sent_last_hour += 1
            if lead["domain"]:
                contacted.add(lead["domain"])
            print(f"✅ Email accepted by SMTP: {lead['company']} <{email}>")
        else:
            smtp_failures += 1
            lead["status"] = "smtp_error"
            lead["smtp_error"] = detail
            print(f"⚠️ SMTP failed for {email}: {detail}")

    with open(LEADS_JSON, "w", encoding="utf-8") as f:
        json.dump(all_leads, f, ensure_ascii=False, indent=2)

    fields = ["timestamp", "company", "email", "domain", "url", "need", "sector_hint", "score", "source", "email_source_url", "status", "sent_at", "smtp_error"]
    with open(LEADS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_leads)

    save_history(history)
    print(
        f"Searched {len(QUERIES)} queries ({', '.join(f'{k}={v}' for k, v in source_counts.items())}); "
        f"Found {len(all_leads)} buyer leads; qualified {len(qualified)}; sent {sent} emails; "
        f"SMTP failures {smtp_failures}; no email {sum(1 for x in all_leads if not x.get('email'))}"
    )


if __name__ == "__main__":
    main()
, decoded)
                if not match:
                    continue
                flags = match.group(1).lower()
                raw_name = match.group(2).strip()
                if raw_name.startswith('"') and raw_name.endswith('"'):
                    raw_name = raw_name[1:-1].replace(r'\"', '"')
                folders.append((raw_name, flags))

            # Prefer the server's special-use Sent mailbox if it advertises one.
            for name, flags in folders:
                if r"\sent" in flags:
                    folder = name
                    break
            if not folder:
                for name, _flags in folders:
                    lname = name.lower()
                    if lname in ("sent", "inbox.sent", "sent messages", "sent items", "envoyés", "envoyÃ©s"):
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


def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user
    if not all([host, user, password, sender]):
        return False, "SMTP credentials not configured"
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
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

            # SMTP acceptance and recipient delivery are different statuses. Save a
            # copy in Roundcube's Sent folder separately; do not fail sending if IMAP fails.
            saved, folder_or_error = save_to_sent_folder(msg)
            if saved:
                print(f"📨 Copy saved to IMAP Sent folder: {folder_or_error}")
            else:
                print(f"⚠️ Email accepted by SMTP, but copy not saved to Sent: {folder_or_error}")
            return True, "accepted_by_smtp"
        except Exception as e:
            if attempt < SMTP_RETRIES:
                time.sleep(2 * attempt)
            else:
                return False, str(e)
    return False, "unknown SMTP error"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history()
    contacted = {str(k).lower() for k in history.keys()}
    for value in history.values():
        if isinstance(value, dict):
            for field in ("email", "domain"):
                if value.get(field):
                    contacted.add(str(value[field]).lower())

    all_leads = []
    seen = set()
    source_counts = {}
    print("Starting Morocco-only B2B buyer discovery with free search sources...")

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
            if key in seen or key in contacted:
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
            email = discover_email(url)
            if not email:
                email = public_email(company, url)
            lead = {
                "timestamp": now_iso(),
                "company": company,
                "email": email,
                "domain": d,
                "url": url,
                "need": detect_need(evidence),
                "sector_hint": sector_hint,
                "score": s,
                "source": source,
                "status": "qualified" if email else "no_email",
            }
            all_leads.append(lead)
            if len(all_leads) >= 30:
                break
        if len(all_leads) >= 30:
            break


    # Add verified official-contact fallbacks when search engines return little or no data.
    # Skip every company or email already contacted; never re-send on later scheduled runs.
    seeded_added = 0
    existing_domains_with_email = {
        x.get("domain", "").lower() for x in all_leads if x.get("email")
    }
    existing_emails = {x.get("email", "").lower() for x in all_leads if x.get("email")}
    for seed in SEEDED_PUBLIC_CONTACTS:
        email = seed["email"].lower()
        d = seed["domain"].lower()
        if email in contacted or d in contacted or email in existing_emails or d in existing_domains_with_email:
            continue
        lead = {
            "timestamp": now_iso(),
            **seed,
            "source": "verified-official-contact-fallback",
            "status": "qualified",
        }
        all_leads.append(lead)
        existing_emails.add(email)
        existing_domains_with_email.add(d)
        seeded_added += 1
    print(f"Added {seeded_added} verified official-contact fallback prospects")

    all_leads.sort(key=lambda x: (x["score"], bool(x["email"])), reverse=True)
    qualified = [x for x in all_leads if x["email"] and x["score"] >= 20]
    sent = 0
    smtp_failures = 0
    sent_last_hour = count_sends_in_last_hour(history)
    if sent_last_hour >= MAX_EMAILS_PER_HOUR:
        print(
            f"⏸️ Hourly safety limit already reached "
            f"({sent_last_hour}/{MAX_EMAILS_PER_HOUR}); no emails will be sent this run"
        )

    for lead in qualified:
        if sent >= MAX_EMAILS:
            break
        if sent_last_hour >= MAX_EMAILS_PER_HOUR:
            print(
                f"⏸️ Hourly safety limit reached "
                f"({sent_last_hour}/{MAX_EMAILS_PER_HOUR}); remaining leads kept for a later run"
            )
            break
        email = lead["email"].lower()
        if email in contacted or lead["domain"] in contacted:
            continue
        subject, body = build_message(lead["company"], lead["need"])
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
            sent_last_hour += 1
            if lead["domain"]:
                contacted.add(lead["domain"])
            print(f"✅ Email accepted by SMTP: {lead['company']} <{email}>")
        else:
            smtp_failures += 1
            lead["status"] = "smtp_error"
            lead["smtp_error"] = detail
            print(f"⚠️ SMTP failed for {email}: {detail}")

    with open(LEADS_JSON, "w", encoding="utf-8") as f:
        json.dump(all_leads, f, ensure_ascii=False, indent=2)

    fields = ["timestamp", "company", "email", "domain", "url", "need", "sector_hint", "score", "source", "email_source_url", "status", "sent_at", "smtp_error"]
    with open(LEADS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_leads)

    save_history(history)
    print(
        f"Searched {len(QUERIES)} queries ({', '.join(f'{k}={v}' for k, v in source_counts.items())}); "
        f"Found {len(all_leads)} buyer leads; qualified {len(qualified)}; sent {sent} emails; "
        f"SMTP failures {smtp_failures}; no email {sum(1 for x in all_leads if not x.get('email'))}"
    )


if __name__ == "__main__":
    main()
