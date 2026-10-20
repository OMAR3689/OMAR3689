import csv, json, os, re
from datetime import datetime, timezone
from urllib.request import Request, urlopen

API_KEY = os.environ.get("TAVILY_API_KEY", "")
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")

QUERIES = [
    '"recherche transporteur" OR "prestataire transport" Maroc',
    '"appel d offres" transporteur logistique Maroc',
    '"recherche transporteur" France logistique',
    '"prestataire transport" Belgique logistique',
    '"carrier needed" OR "transport provider" Germany',
    '"freight tender" OR "transport tender" Europe',
    '"sous-traitant transport" "recherche"',
    '"besoin de transport" entreprise Maroc',
]

SIGNALS = {
    "recherche transporteur": 35,
    "prestataire transport": 35,
    "appel d'offres": 30,
    "appel d offres": 30,
    "freight tender": 30,
    "transport tender": 30,
    "carrier needed": 35,
    "transport provider": 30,
    "sous-traitant": 20,
    "affrètement": 20,
    "subcontractor": 20,
}

def tavily_search(query):
    if not API_KEY:
        raise RuntimeError("TAVILY_API_KEY is required")
    payload = json.dumps({"api_key": API_KEY, "query": query, "search_depth": "advanced", "max_results": 10, "include_answer": False}).encode()
    req = Request("https://api.tavily.com/search", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("results", [])

def score(text):
    t = text.lower()
    points = sum(v for k, v in SIGNALS.items() if k in t)
    if any(x in t for x in ["2026", "2027", "urgent", "immediate"]): points += 10
    return min(points, 100)

def main():
    leads = {}
    for q in QUERIES:
        for item in tavily_search(q):
            title = item.get("title", "").strip()
            url = item.get("url", "").strip()
            content = item.get("content", "")
            key = url or title.lower()
            if not key: continue
            text = f"{title} {content}"
            s = score(text)
            if s < 30: continue
            leads[key] = {
                "company_or_title": title,
                "need": content[:1000].replace("\n", " "),
                "score": s,
                "source_url": url,
                "country_hint": next((c for c in ["Maroc", "France", "Belgique", "Germany", "Allemagne", "Spain", "Italy"] if c.lower() in text.lower()), ""),
                "found_at": datetime.now(timezone.utc).isoformat(),
            }
    rows = sorted(leads.values(), key=lambda x: x["score"], reverse=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "leads.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "leads.csv"), "w", encoding="utf-8", newline="") as f:
        fields = ["company_or_title", "need", "score", "source_url", "country_hint", "found_at"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f"Found {len(rows)} qualified leads")

if __name__ == "__main__": main()
