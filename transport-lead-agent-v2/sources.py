"""
Sources gratuites et prudentes pour TRMB Lead Agent V2.
- Portail officiel marocain des marchés publics, en lecture seule.
- Crawl des sites d'entreprises uniquement si robots.txt l'autorise.
- Délai par hôte, limites de taille et nouvelles tentatives en cas de limitation.
- Aucun login, aucune session authentifiée, aucun scraping LinkedIn.
"""
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib import robotparser

USER_AGENT = "TRMB-Lead-Agent/2.1 (+https://trmb.ma/; contact: info@trmb.ma)"
REQUEST_DELAY = 3.0
TIMEOUT = 20
MAX_RETRIES = 3
MAX_HTML_BYTES = 2_000_000

# URL officielle vérifiée du portail; la recherche avancée utilise le paramètre keyWord.
MP_SEARCH_URL = "https://www.marchespublics.gov.ma/index.php"
MP_KEYWORDS = ("transport", "logistique", "convoyage", "flotte", "marchandises", "livraison")
CONTACT_HINTS = (
    "contact", "nous-contacter", "contactez", "contact-us",
    "a-propos", "about", "mentions-legales", "mentions-légales",
)
GENERIC_PREFIXES = {
    "info", "contact", "commercial", "direction", "achats",
    "administration", "accueil", "secretariat", "secrétariat",
    "devis", "sales", "ventes", "logistique", "transport",
    "procurement", "supplychain", "service",
}
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
BAD_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
MP_HOST = "www.marchespublics.gov.ma"

_robots_cache = {}
_last_hit = {}


def _host(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _normal_text(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip().lower().replace("’", "'")


def allowed_by_robots(url):
    """Fail closed: si robots.txt ne peut pas être lu, ne pas crawler ce site."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots_cache:
        rp = robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
            _robots_cache[base] = rp
        except Exception as exc:
            print(f"⏭️ robots.txt inaccessible; crawl désactivé pour {base}: {exc}")
            _robots_cache[base] = None
    rp = _robots_cache[base]
    if rp is None:
        return False
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return False


def polite_get(url):
    """GET HTML avec vérification robots.txt, délai par hôte et retry limité."""
    if not allowed_by_robots(url):
        print(f"⏭️ robots.txt interdit ou invérifiable: {url}")
        return None

    host = _host(url)
    wait = REQUEST_DELAY - (time.monotonic() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8,en;q=0.6",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                _last_hit[host] = time.monotonic()
                content_type = resp.headers.get("Content-Type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    print(f"⏭️ Type de contenu ignoré ({content_type or 'inconnu'}): {url}")
                    return None
                payload = resp.read(MAX_HTML_BYTES + 1)
                if len(payload) > MAX_HTML_BYTES:
                    print(f"⏭️ Page trop volumineuse (> {MAX_HTML_BYTES} octets): {url}")
                    return None
                charset_match = re.search(r"charset=([\w.-]+)", content_type)
                charset = charset_match.group(1) if charset_match else "utf-8"
                return payload.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            _last_hit[host] = time.monotonic()
            if exc.code in (429, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(10 * (2 ** attempt))
                continue
            print(f"⚠️ HTTP {exc.code}: {url}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _last_hit[host] = time.monotonic()
            if attempt < MAX_RETRIES - 1:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"⚠️ Erreur réseau: {url} ({exc})")
            return None
    return None


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href:
            label = " ".join(" ".join(self._text).split())
            self.links.append((self._href, label))
            self._href = None
            self._text = []


class _TableRowParser(HTMLParser):
    """Capture le texte et les liens de chaque ligne de résultats HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._href = None
        self._anchor_text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "tr" and self._row is None:
            self._row = {"parts": [], "links": []}
        elif tag == "a" and self._row is not None and attrs.get("href"):
            self._href = attrs["href"]
            self._anchor_text = []

    def handle_data(self, data):
        if self._row is not None:
            self._row["parts"].append(data)
        if self._href is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self._row is not None and self._href:
            label = re.sub(r"\s+", " ", " ".join(self._anchor_text)).strip()
            self._row["links"].append((self._href, label))
            self._href = None
            self._anchor_text = []
        elif tag == "tr" and self._row is not None:
            self._row["text"] = re.sub(r"\s+", " ", " ".join(self._row["parts"])).strip()
            self._row.pop("parts", None)
            if self._row["text"]:
                self.rows.append(self._row)
            self._row = None


def extract_generic_emails(html, site_domain):
    """Retourne uniquement les adresses génériques publiques du même domaine."""
    site_domain = _host("https://" + site_domain) if "://" not in site_domain else _host(site_domain)
    found = set()
    for email in EMAIL_RE.findall(html or ""):
        email = email.lower().strip(".,;:()[]<>")
        if email.endswith(BAD_SUFFIXES):
            continue
        local, _, dom = email.partition("@")
        dom = dom.removeprefix("www.")
        same_domain = dom == site_domain or dom.endswith("." + site_domain)
        if same_domain and local in GENERIC_PREFIXES:
            found.add(email)
    return sorted(found)


def find_official_contacts(site_url, max_pages=4):
    """Crawl limité du site officiel et de ses pages contact, si robots.txt l'autorise."""
    original_host = _host(site_url)
    if not original_host:
        return []

    to_visit = [site_url]
    seen = set()
    emails = set()
    while to_visit and len(seen) < max(1, min(int(max_pages), 8)):
        url = to_visit.pop(0)
        parts = urllib.parse.urlsplit(url)
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        if url in seen or _host(url) != original_host:
            continue
        seen.add(url)

        html = polite_get(url)
        if not html:
            continue
        emails.update(extract_generic_emails(html, original_host))
        if emails:
            break

        parser = _LinkParser()
        parser.feed(html)
        for href, label in parser.links:
            absolute = urllib.parse.urljoin(url, href)
            absolute_parts = urllib.parse.urlsplit(absolute)
            absolute = urllib.parse.urlunsplit((
                absolute_parts.scheme, absolute_parts.netloc,
                absolute_parts.path, absolute_parts.query, ""
            ))
            if _host(absolute) != original_host or absolute in seen:
                continue
            hint_text = _normal_text(absolute + " " + label)
            if any(hint in hint_text for hint in CONTACT_HINTS):
                to_visit.append(absolute)
    return sorted(emails)


def _tender_search_url(keyword):
    params = {
        "AllCons": "",
        "EnCours": "",
        "page": "entreprise.EntrepriseAdvancedSearch",
        "searchAnnCons": "",
        "keyWord": keyword,
    }
    return MP_SEARCH_URL + "?" + urllib.parse.urlencode(params)


def _parse_tender_row(row, keyword, listing_url):
    row_text = row.get("text", "")
    normal = _normal_text(row_text)
    if not any(_normal_text(k) in normal for k in MP_KEYWORDS):
        return None
    if len(row_text) < 35:
        return None

    title_match = re.search(
        r"\bObjet\s*:\s*(.+?)(?=\s*(?:Acheteur public\s*:|Lieu d'execution|Date limite|$))",
        row_text, flags=re.IGNORECASE
    )
    title = title_match.group(1).strip(" :-") if title_match else ""
    if not title:
        labels = [label for _, label in row.get("links", []) if len(label.strip()) > 12]
        title = labels[0] if labels else row_text[:240]
    title = re.sub(r"\s+", " ", title)[:500]

    buyer_match = re.search(
        r"Acheteur public\s*:\s*(.+?)(?=\s*(?:Lieu d'execution|Date limite|$))",
        row_text, flags=re.IGNORECASE
    )
    buyer = re.sub(r"\s+", " ", buyer_match.group(1)).strip(" :-")[:300] if buyer_match else ""

    deadline_match = re.search(
        r"Date limite(?: de remise des plis)?\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        row_text, flags=re.IGNORECASE
    )
    dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", row_text)
    deadline = deadline_match.group(1) if deadline_match else (dates[-1] if dates else "")
    if deadline:
        try:
            deadline_date = datetime.strptime(deadline, "%d/%m/%Y").date()
            if deadline_date < datetime.now().date():
                return None
            tender_status = "date_limite_future"
        except ValueError:
            tender_status = "date_limite_a_verifier"
    else:
        tender_status = "date_limite_inconnue_a_verifier"

    detail_url = ""
    for href, label in row.get("links", []):
        absolute = urllib.parse.urljoin(listing_url, href)
        parts = urllib.parse.urlsplit(absolute)
        if parts.scheme not in ("http", "https") or _host(absolute) != _host(MP_SEARCH_URL):
            continue
        path = parts.path.lower()
        if any(path.endswith(ext) for ext in BAD_SUFFIXES):
            continue
        combined = (absolute + " " + label).lower()
        if "entreprise.entrepriseadvancedsearch" in combined and "keyWord" not in combined:
            continue
        if "detail" in combined or "consultation" in combined or len(label.strip()) > 12:
            detail_url = absolute
            break
    result = {
        "title": title,
        "buyer": buyer,
        "deadline": deadline,
        "status": tender_status,
        "keyword": keyword,
        "url": detail_url or listing_url,
        "source": "Portail Marocain des Marchés Publics",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    if not buyer and not title:
        return None
    return result


def find_tenders(keywords=MP_KEYWORDS, max_per_keyword=15):
    """
    Recherche les avis publics contenant des mots liés au transport.
    Les marchés publics sont retournés comme opportunités de soumission,
    jamais comme prospects à qui envoyer automatiquement un email commercial.
    """
    found = {}
    official_host = _host(MP_SEARCH_URL)
    for keyword in keywords:
        listing_url = _tender_search_url(keyword)
        html = polite_get(listing_url)
        if not html:
            continue
        parser = _TableRowParser()
        try:
            parser.feed(html)
        except Exception as exc:
            print(f"⚠️ Impossible de lire les lignes d'appels d'offres ({keyword}): {exc}")
            continue

        count_for_keyword = 0
        for row in parser.rows:
            tender = _parse_tender_row(row, keyword, listing_url)
            if not tender:
                continue
            dedup_key = _normal_text(tender["title"]) + "|" + _normal_text(tender["buyer"]) + "|" + tender["deadline"]
            if dedup_key not in found:
                found[dedup_key] = tender
            count_for_keyword += 1
            if count_for_keyword >= max_per_keyword:
                break

    results = list(found.values())
    print(f"📑 Marchés publics: {len(results)} opportunités retenues après déduplication et filtre de date.")
    return results
