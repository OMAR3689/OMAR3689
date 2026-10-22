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
import xml.etree.ElementTree as ET
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
    """Fail closed, avec timeout explicite pour ne pas bloquer une exécution."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots_cache:
        robots_url = base + "/robots.txt"
        req = urllib.request.Request(
            robots_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read(100_000).decode("utf-8", errors="replace")
            rp = robotparser.RobotFileParser()
            rp.set_url(robots_url)
            rp.parse(raw.splitlines())
            _robots_cache[base] = rp
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                # An absent robots.txt is "unavailable", not "disallow all":
                # treat the site as having no published robot rules.
                rp = robotparser.RobotFileParser()
                rp.set_url(robots_url)
                rp.parse([])
                _robots_cache[base] = rp
                print(f"ℹ️ robots.txt absent (HTTP {exc.code}); no crawl rules published by {base}")
            else:
                # Keep the conservative fail-closed policy for 401/403/429/5xx.
                print(f"⏭️ robots.txt inaccessible; crawl désactivé pour {base}: HTTP {exc.code}")
                _robots_cache[base] = None
        except Exception as exc:
            # Timeout or unknown network error: do not assume permission.
            print(f"⏭️ robots.txt inaccessible; crawl désactivé pour {base}: {exc}")
            _robots_cache[base] = None
    rp = _robots_cache[base]
    if rp is None:
        return False
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return False


def _polite_delay(url):
    """Use the site-declared Crawl-delay when available, never below 3 seconds."""
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    rp = _robots_cache.get(base)
    delay = REQUEST_DELAY
    if rp is not None:
        try:
            declared = rp.crawl_delay(USER_AGENT)
            if declared is not None:
                delay = max(delay, float(declared))
        except Exception:
            pass
    return delay


def polite_get(url):
    """GET HTML avec vérification robots.txt, délai par hôte et retry limité."""
    if not allowed_by_robots(url):
        print(f"⏭️ robots.txt interdit ou invérifiable: {url}")
        return None

    host = _host(url)
    wait = _polite_delay(url) - (time.monotonic() - _last_hit.get(host, 0))
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


def find_official_contact_records(site_url, max_pages=4):
    """Return public generic contacts with the exact official page URL where each appeared."""
    original_host = _host(site_url)
    if not original_host:
        return []

    to_visit = [site_url]
    seen = set()
    emails = {}
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
        for email in extract_generic_emails(html, original_host):
            emails.setdefault(email, url)
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
    return [
        {"email": email, "source_url": source_url}
        for email, source_url in sorted(emails.items())
    ]


def find_official_contacts(site_url, max_pages=4):
    """Backward-compatible email-only wrapper."""
    return [
        item["email"]
        for item in find_official_contact_records(site_url, max_pages=max_pages)
    ]


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


def _parse_indexed_tender(item, keyword):
    """Parse a specific indexed tender; avoid generic listings and publication-date confusion."""
    url = str(item.get("url", "")).strip()
    title = re.sub(r"\s+", " ", str(item.get("title", "") or "")).strip()
    description = re.sub(r"\s+", " ", str(item.get("content", "") or "")).strip()
    if not url or not title:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or _host(url) != _host(MP_SEARCH_URL):
        return None

    query_params = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    page_type = (query_params.get("page", [""])[0] or "").lower()
    is_purchase_detail = bool(re.search(
        r"/bdc/entreprise/consultation/show/\d+/?$", parts.path, flags=re.IGNORECASE
    ))
    has_consultation_ref = any(
        str(value).strip()
        for key in ("refConsultation", "id")
        for value in query_params.get(key, [])
    )
    is_named_detail_page = (
        ("detail" in page_type and "consultation" in page_type)
        or ("suivi" in page_type and "consultation" in page_type)
    )
    # A search/listing URL itself is not an opportunity. Require a concrete
    # purchase-detail path or a consultation/detail ID on an official page.
    if not (is_purchase_detail or (has_consultation_ref and is_named_detail_page)):
        return None

    text = title + " " + description
    normal = _normal_text(text)
    # The search query was already restricted to this keyword and official host.
    # Index snippets may omit the matching body text, especially on purchase notices,
    # so require a known query keyword plus a specific detail URL rather than
    # discarding a valid official record solely because its snippet is sparse.
    known_keywords = {_normal_text(term) for term in MP_KEYWORDS}
    if _normal_text(keyword) not in known_keywords:
        return None
    if any(marker in normal for marker in (
        "avis de resultat definitif", "resultat definitif", "pv d'ouverture",
        "avis d'attribution"
    )):
        return None

    generic_titles = {
        "marches publics electroniques",
        "portail marocain des marches publics",
        "marches publics",
    }
    stop_labels = (
        r"Acheteur public|Date limite|Date mise en ligne|Date de mise en ligne|"
        r"Date publication|Date de publication|Type d'annonce|Procedure|"
        r"Categorie principale|Lieu d'execution|Objet|"
        r"Date d'ouverture|Date de remise"
    )
    objective_match = re.search(
        rf"\bobjet(?:\s+de\s+(?:la\s+)?(?:consultation|l'appel d'offres|l'operation))?"
        rf"\s*[:\-]\s*(.+?)(?=\s*(?:{stop_labels})\b|\s*$)",
        text, flags=re.IGNORECASE
    )
    if _normal_text(title) in generic_titles:
        objective = re.sub(r"\s+", " ", objective_match.group(1)).strip(" :-") if objective_match else ""
        if not objective or _normal_text(objective) in generic_titles:
            # A generic portal title with no indexed object is not useful enough
            # to present as a named tender candidate.
            return None
        title = objective[:500]

    deadline_match = re.search(
        r"(?:date\s+limite(?:\s+de\s+(?:remise\s+des\s+plis|depot|reception\s+des\s+offres))?"
        r"|deadline)\s*[:\-]?\s*"
        r"(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2})",
        text, flags=re.IGNORECASE
    )
    deadline = ""
    deadline_status = "date_limite_inconnue_a_verifier"
    if deadline_match:
        deadline = deadline_match.group(1)
        parsed_date = None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                parsed_date = datetime.strptime(deadline, fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            deadline_status = "date_limite_a_verifier"
        elif parsed_date < datetime.now().date():
            return None
        else:
            deadline_status = "date_limite_future"

    buyer_match = re.search(
        rf"Acheteur public\s*[:\-]?\s*(.+?)(?=\s*(?:{stop_labels})\b|\s*$)",
        text, flags=re.IGNORECASE
    )
    buyer = re.sub(r"\s+", " ", buyer_match.group(1)).strip(" :-")[:300] if buyer_match else ""
    # Keep the buyer field clean when the search snippet has adjacent metadata.
    buyer = re.sub(
        r"\s+(?:Date mise en ligne|Date de mise en ligne|Date publication|Date de publication)\b.*$",
        "", buyer, flags=re.IGNORECASE
    ).strip(" :-")
    return {
        "title": title[:500],
        "buyer": buyer,
        "deadline": deadline,
        "status": deadline_status,
        "keyword": keyword,
        "url": url,
        "source": "Index public du Portail Marocain des Marchés Publics",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


class _SearchResultLinkParser(HTMLParser):
    """Extract ordinary result links from DuckDuckGo's public HTML results."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._href = ""
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attrs = dict(attrs)
        classes = str(attrs.get("class", "")).split()
        href = str(attrs.get("href", "")).strip()
        if "result__a" in classes and href:
            self._href = href
            self._parts = []

    def handle_data(self, data):
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or not self._href:
            return
        title = re.sub(r"\s+", " ", " ".join(self._parts)).strip()
        parsed = urllib.parse.urlsplit(self._href)
        query = urllib.parse.parse_qs(parsed.query)
        href = query.get("uddg", [self._href])[0]
        if title and href.startswith(("http://", "https://")):
            self.results.append({"title": title, "url": href, "content": ""})
        self._href = ""
        self._parts = []


def _ddgs_index_search(query):
    """Search a public search index through DDGS without fetching target pages."""
    try:
        from ddgs import DDGS
        raw = DDGS(timeout=10).text(
            query, region="ma-fr", safesearch="off", max_results=10, backend="auto"
        )
        out = []
        for item in raw or []:
            url = str(item.get("href") or item.get("url") or "").strip()
            title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
            content = re.sub(r"\s+", " ", str(item.get("body") or item.get("content") or "")).strip()
            if url.startswith(("http://", "https://")) and title:
                out.append({"title": title, "url": url, "content": content})
        return out
    except Exception as exc:
        print(f"⚠️ DDGS index des marchés publics indisponible: {exc}")
        return []


def _bing_rss_index_search(query):
    """Query Bing's public RSS index only; do not fetch result pages."""
    search_url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read(500_000)
        root = ET.fromstring(payload)
        return [
            {
                "title": item.findtext("title") or "",
                "url": item.findtext("link") or "",
                "content": item.findtext("description") or "",
            }
            for item in root.findall(".//item")
        ]
    except Exception as exc:
        print(f"⚠️ Bing RSS index des marchés publics indisponible: {exc}")
        return []


def _duckduckgo_html_index_search(query):
    """Fallback to public DuckDuckGo HTML search results."""
    search_url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read(500_000).decode("utf-8", errors="replace")
        parser = _SearchResultLinkParser()
        parser.feed(payload)
        return parser.results
    except Exception as exc:
        print(f"⚠️ DuckDuckGo HTML index des marchés publics indisponible: {exc}")
        return []


def _find_indexed_tenders(keywords):
    """
    Find official procurement notices from public search indexes when the portal
    itself cannot be read. Only URLs hosted by the official portal are accepted.
    Search the newer purchase-order detail path and the classic consultation pages.
    Stop trying engines for a keyword as soon as a valid official notice is found.
    """
    found = {}
    engines = (
        ("DDGS", _ddgs_index_search),
        ("Bing RSS", _bing_rss_index_search),
        ("DuckDuckGo HTML", _duckduckgo_html_index_search),
    )
    for keyword in list(keywords):
        query_variants = (
            f'site:{MP_HOST}/bdc/entreprise/consultation/show "{keyword}"',
            f'site:{MP_HOST} "{keyword}" ("Acheteur public" OR "Objet" OR "Date limite")',
            f'site:{MP_HOST} "{keyword}"',
        )
        keyword_found = False
        for query in query_variants:
            for engine_name, search_fn in engines:
                try:
                    items = search_fn(query)
                except Exception as exc:
                    print(f"⚠️ Index public indisponible ({engine_name}, {keyword}): {exc}")
                    continue
                accepted = []
                for item in items or []:
                    tender = _parse_indexed_tender(item, keyword)
                    if not tender:
                        continue
                    dedup_key = (
                        _normal_text(tender["title"]) + "|"
                        + _normal_text(tender["buyer"]) + "|"
                        + tender["deadline"]
                    )
                    if dedup_key not in found:
                        tender["index_engine"] = engine_name
                        found[dedup_key] = tender
                        accepted.append(tender)
                if accepted:
                    print(
                        f"📑 {engine_name}: {len(accepted)} avis officiels indexés "
                        f"pour le mot-clé « {keyword} »."
                    )
                    keyword_found = True
                    break
            if keyword_found:
                break
    print(
        f"📑 Index public du portail officiel: {len(found)} opportunités candidates "
        "sur DDGS, Bing RSS et DuckDuckGo HTML; dates/éligibilité à vérifier."
    )
    return list(found.values())

def find_tenders(keywords=MP_KEYWORDS, max_per_keyword=15):
    """
    Read the official procurement portal when its published crawl rules allow it.
    If robots.txt/network blocks the portal, use public search indexes and keep only official-portal URLs.
    Tenders are stored separately and never automatically emailed as sales leads.
    """
    found = {}
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

    if not found:
        for tender in _find_indexed_tenders(keywords):
            dedup_key = _normal_text(tender["title"]) + "|" + _normal_text(tender["buyer"]) + "|" + tender["deadline"]
            found.setdefault(dedup_key, tender)

    results = list(found.values())
    print(f"📑 Marchés publics: {len(results)} opportunités retenues après déduplication et filtre de date.")
    return results
