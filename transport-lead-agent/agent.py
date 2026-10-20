import csv, json, os, re, smtplib, ssl, time, imaplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import Request, urlopen

TAVILY_KEY=os.environ.get("TAVILY_API_KEY","").strip()
OUT_DIR=os.path.join(os.path.dirname(__file__),"data")
HISTORY_FILE=os.path.join(OUT_DIR,"contacted.json")
MAX_EMAILS=int(os.environ.get("MAX_EMAILS_PER_RUN","5"))
PRESENTATION_URL=os.environ.get("PRESENTATION_URL","https://trmb.ma/")
SMTP_RETRIES=int(os.environ.get("SMTP_RETRIES","3"))

# Fewer, more targeted searches = less Tavily usage and fewer transport-company results.
QUERIES=[
    '"demande de devis transport" Maroc usine OR industriel OR distribution',
    '"besoin de transport" Maroc entreprise marchandises usine',
    '"recherche prestataire" transport Maroc entreprise',
    '"appel d offres" transport Maroc entreprise industriel',
    '"transport quotidien" Maroc usine OR salariés OR personnel',
    '"livraison" Maroc entreprise distribution marchandises',
    '"affrètement" Maroc industriel chargeur importateur',
    '"location véhicules avec chauffeur" Maroc entreprise'
]

GENERIC_EMAIL_PREFIXES={"noreply","no-reply","donotreply","do-not-reply","mailer-daemon"}
BAD_DOMAINS={"linkedin.com","facebook.com","instagram.com","youtube.com","gmail.com","yahoo.com","outlook.com","hotmail.com"}
CONTACT_PATHS=("contact","contact-us","nous-contacter","mentions-legales","a-propos","about")

# Signals that indicate a company may actually BUY transport/logistics services.
BUYER_SIGNALS=(
    "demande de devis", "demander un devis", "besoin de transport", "besoin transport",
    "recherche prestataire", "recherche un prestataire", "appel d'offre", "appel d offres",
    "sous-traitance", "transport quotidien", "navette", "livraison", "distribution",
    "affrètement", "chargeur", "location de véhicules", "véhicules avec chauffeur",
    "transport des salariés", "transport du personnel", "flux de marchandises",
    "approvisionnement", "expédition", "importation", "exportation"
)

# Strong signs that the discovered page is itself a transport/logistics provider.
PROVIDER_SIGNALS=(
    "société de transport", "societe de transport", "transporteur routier",
    "transporteur international", "entreprise de transport", "transport & logistique",
    "transport et logistique", "prestataire logistique", "freight forwarder",
    "commissionnaire de transport", "notre flotte", "notre flotte de camions",
    "nos camions", "semi-remorques", "camions 8x4", "transport de marchandises",
    "transport national et international", "solution de transport", "services de transport"
)

# Directory/media/lead-list pages are not prospects themselves.
DIRECTORY_SIGNALS=(
    "fournisseurs transport maroc", "annuaire", "liste des fournisseurs",
    "top fournisseurs", "comparatif", "magazine", "actualités", "actualites",
    "procurement.ma/fournisseurs", "mordorintelligence", "guide"
)

TARGET_SECTOR_SIGNALS=(
    "usine", "industriel", "industrie", "agroalimentaire", "agricole", "ferme",
    "distribution", "grossiste", "retail", "supermarché", "supermarche", "automobile",
    "pharmaceutique", "pharma", "construction", "chantier", "bâtiment", "batiment",
    "importateur", "exportateur", "fabrication", "production", "entrepôt", "entrepot",
    "zone industrielle", "plateforme", "commerce"
)

def now_iso(): return datetime.now(timezone.utc).isoformat()

def http_json(url,payload,headers):
    req=Request(url,data=json.dumps(payload).encode(),headers=headers,method="POST")
    try:
        with urlopen(req,timeout=45) as r:return json.loads(r.read().decode())
    except HTTPError as e:raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except URLError as e:raise RuntimeError(f"Network error: {e.reason}")

def tavily_search(q):
    if not TAVILY_KEY:raise RuntimeError("TAVILY_API_KEY is required")
    return http_json(
        "https://api.tavily.com/search",
        {"query":q,"search_depth":"basic","max_results":8,"include_answer":False},
        {"Content-Type":"application/json","Authorization":f"Bearer {TAVILY_KEY}"}
    ).get("results",[])

def fetch_page(url):
    try:
        with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0 TRMB lead research bot"}),timeout=12) as r:
            raw=r.read(400000).decode("utf-8",errors="ignore")
        text=re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>"," ",raw,flags=re.I)
        text=re.sub(r"\s+"," ",text).strip()
        return text[:100000],re.findall(r'href=["\']([^"\']+)["\']',raw,flags=re.I)
    except Exception:return "",[]

def extract_emails(text):
    out=[]
    for e in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",text or ""):
        e=e.lower().strip(".,;:()[]<>")
        if e.split("@",1)[0] not in GENERIC_EMAIL_PREFIXES and e not in out:out.append(e)
    return out

def domain(url):
    try:return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:return ""

def usable_email(e,url=""):
    if not e or "@" not in e:return False
    d=e.split("@",1)[1].lower()
    return d not in BAD_DOMAINS and (not url or domain(url) not in BAD_DOMAINS)

def discover_email(url):
    if not url:return ""
    text,links=fetch_page(url);pool=[url]
    for link in links:
        absolute=urljoin(url,link)
        if link.lower().startswith("mailto:"):
            for e in extract_emails(unquote(link[7:].split("?",1)[0])):
                if usable_email(e,url):return e
        p=urlparse(absolute);path=p.path.lower().strip("/")
        if p.scheme in ("http","https") and (path in CONTACT_PATHS or any(path.endswith("/"+x) for x in CONTACT_PATHS)):pool.append(absolute)
    for u in list(dict.fromkeys(pool))[:8]:
        text,links=fetch_page(u)
        for e in extract_emails(text):
            if usable_email(e,u):return e
    return ""

def public_email(title,url):
    q=f'site:{domain(url)} email contact' if domain(url) else f'"{title}" Maroc email contact'
    try:r=tavily_search(q)
    except Exception:return ""
    for x in r:
        for e in extract_emails(f"{x.get('title','')} {x.get('content','')}"):
            if usable_email(e,x.get("url","") or url):return e
    return ""

def score(text):
    t=text.lower()
    buyer=sum(15 for x in BUYER_SIGNALS if x in t)
    sector=sum(8 for x in TARGET_SECTOR_SIGNALS if x in t)
    provider=sum(25 for x in PROVIDER_SIGNALS if x in t)
    directory=sum(40 for x in DIRECTORY_SIGNALS if x in t)
    return max(0,min(100,buyer+sector-provider-directory))

def is_bad_prospect(title,content,url):
    t=f"{title} {content} {url}".lower()
    if any(x in t for x in DIRECTORY_SIGNALS):return True
    # Do not pitch a transport provider as if it were a transport buyer.
    provider_hits=sum(1 for x in PROVIDER_SIGNALS if x in t)
    buyer_hits=sum(1 for x in BUYER_SIGNALS if x in t)
    if provider_hits >= 2 and buyer_hits <= 2:return True
    return False

def clean_context(content):
    text=re.sub(r"[#*_]+"," ",content or "")
    text=re.sub(r"\s+"," ",text).strip()
    # Keep only a compact, readable snippet for the email.
    return text[:500]

def detect_need(content):
    t=(content or "").lower()
    if any(x in t for x in ("transport du personnel","transport des salariés","navette")):return "transport de personnel / navettes"
    if any(x in t for x in ("livraison","distribution","approvisionnement")):return "livraison / distribution"
    if any(x in t for x in ("affrètement","chargeur","flux de marchandises")):return "affrètement / flux de marchandises"
    if any(x in t for x in ("importation","exportation","expédition")):return "transport lié aux flux import/export"
    return "transport et logistique"

def qualify(title,content,url,email):
    text=f"{title} {content}";t=text.lower();s=score(text)
    intent=any(x in t for x in BUYER_SIGNALS)
    sector=any(x in t for x in TARGET_SECTOR_SIGNALS)
    bad=is_bad_prospect(title,content,url)
    email=email or ""
    ok=bool(email and usable_email(email,url) and intent and not bad and (sector or s>=35))
    need=detect_need(content)
    context=clean_context(content)
    company=title.strip() or "votre entreprise"
    body=(
        f"Bonjour,\n\n"
        f"Je me permets de vous contacter au nom de TRMB SERVICES. "
        f"Nous accompagnons les entreprises au Maroc sur leurs besoins de {need}.\n\n"
        f"En consultant les informations publiques concernant {company}, nous avons identifié un besoin potentiel lié à {need}. "
        f"Nous pouvons étudier vos flux, vos fréquences et vos contraintes afin de vous proposer une solution de transport adaptée.\n\n"
        f"Présentation : {PRESENTATION_URL}\n\n"
        f"Si vous avez un besoin actuel ou à venir, envoyez-nous simplement votre trajet, la marchandise ou le nombre de personnes à transporter, et nous vous répondrons rapidement.\n\n"
        f"Bien cordialement,\n"
        f"TRMB SERVICES\n"
        f"{PRESENTATION_URL}"
    )
    return ok,email,f"intent={'oui' if intent else 'non'}; secteur={'oui' if sector else 'non'}; provider={'oui' if bad else 'non'}; score={s}",body

def load_history():
    try:
        with open(HISTORY_FILE,encoding="utf-8") as f:d=json.load(f);return d if isinstance(d,dict) else {}
    except (FileNotFoundError,json.JSONDecodeError):return {}

def save_history(d):
    with open(HISTORY_FILE,"w",encoding="utf-8") as f:json.dump(d,f,ensure_ascii=False,indent=2)

def history_keys(d):
    k=set(d.keys())
    for v in d.values():
        if isinstance(v,dict):
            if v.get("email"):k.add(v["email"].lower())
            if v.get("domain"):k.add(v["domain"].lower())
    return k

def save_to_sent(raw):
    host=os.environ.get("IMAP_HOST",os.environ.get("SMTP_HOST","")).strip();port=int(os.environ.get("IMAP_PORT","993"));user=os.environ.get("IMAP_USER",os.environ.get("SMTP_USER","")).strip();pw=os.environ.get("IMAP_PASSWORD",os.environ.get("SMTP_PASSWORD",""))
    if not all([host,user,pw]):return False,"IMAP not configured"
    candidates=[x.strip() for x in os.environ.get("IMAP_SENT_FOLDER","Sent").split(",") if x.strip()]
    candidates += ["Sent","INBOX.Sent","INBOX/Sent","Sent Items","Sent Mail","Sent Messages"]
    seen=set()
    try:
        with imaplib.IMAP4_SSL(host,port) as imap:
            imap.login(user,pw)
            for target in candidates:
                if target.lower() in seen:continue
                seen.add(target.lower())
                try:
                    ok,_=imap.append(target,"\\Seen",imaplib.Time2Internaldate(time.time()),raw)
                    if ok=="OK":return True,"saved to Sent: "+target
                except Exception:continue
            return False,"Sent folder not writable/found"
    except Exception as e:return False,f"IMAP Sent save error: {e}"

def send_email(to_addr,subject,body):
    host=os.environ.get("SMTP_HOST","").strip();port=int(os.environ.get("SMTP_PORT","465"));user=os.environ.get("SMTP_USER","").strip();pw=os.environ.get("SMTP_PASSWORD","");sender=os.environ.get("SMTP_FROM","").strip() or user
    if not all([host,user,pw,sender]):return False,"SMTP credentials not configured",{}
    msg=EmailMessage();msg["From"]=sender;msg["To"]=to_addr;msg["Subject"]=subject;msg["Date"]=formatdate(localtime=False);msg["Message-ID"]=make_msgid();msg["Reply-To"]=sender;msg.set_content(body);raw=msg.as_bytes()
    for attempt in range(1,SMTP_RETRIES+1):
        try:
            refused={}
            if port==465:
                with smtplib.SMTP_SSL(host,port,timeout=30,context=ssl.create_default_context()) as s:s.login(user,pw);refused=s.send_message(msg,from_addr=sender,to_addrs=[to_addr])
            else:
                with smtplib.SMTP(host,port,timeout=30) as s:s.ehlo();s.starttls(context=ssl.create_default_context());s.ehlo();s.login(user,pw);refused=s.send_message(msg,from_addr=sender,to_addrs=[to_addr])
            if refused:return False,f"SMTP refused recipient: {refused}",{"message_id":msg["Message-ID"],"sender":sender}
            saved,status=save_to_sent(raw);return True,"sent_and_saved_to_sent" if saved else "sent_smtp_accepted",{"message_id":msg["Message-ID"],"sender":sender,"sent_folder":status,"smtp_accepted":True}
        except (smtplib.SMTPException,OSError) as e:
            if attempt<SMTP_RETRIES:time.sleep(2*attempt)
            else:return False,f"SMTP error: {e}",{"sender":sender}
    return False,"SMTP failed",{}

def main():
    os.makedirs(OUT_DIR,exist_ok=True);history=load_history();known=history_keys(history);leads={};searched=0
    print(f"Starting Morocco-only B2B discovery with {len(QUERIES)} targeted Tavily queries...")
    for q in QUERIES:
        searched+=1
        print(f"Searching: {q}")
        try:results=tavily_search(q)
        except Exception as e:print(f"⚠️ query skipped: {e}");continue
        for item in results:
            title=item.get("title","").strip();url=item.get("url","").strip();content=item.get("content","");text=f"{title} {content}";key=url or title.lower();d=domain(url)
            if not key or key in known or (d and d in known):continue
            if "maroc" not in text.lower() and ".ma" not in url.lower():continue
            if is_bad_prospect(title,content,url):continue
            if not any(x in text.lower() for x in BUYER_SIGNALS):continue
            email=next((e for e in extract_emails(text) if usable_email(e,url)),"") or discover_email(url) or public_email(title,url)
            if email and email in known:continue
            leads[key]={"company_or_title":title,"need":re.sub(r"\s+"," ",content).strip()[:1500],"score":score(text),"source_url":url,"domain":d,"email_found":email,"country_hint":"Maroc","found_at":now_iso()}
    rows=sorted(leads.values(),key=lambda x:x["score"],reverse=True);sent=qualified_count=email_failures=no_email=0
    used_domains=set()
    for lead in rows:
        if sent>=MAX_EMAILS:break
        d=lead.get("domain","")
        if d and d in used_domains:continue
        ok,email,reason,body=qualify(lead["company_or_title"],lead["need"],lead["source_url"],lead.get("email_found",""));lead.update({"qualified":ok,"company":lead["company_or_title"],"email":email,"reason":reason})
        if not email:no_email+=1
        if not ok:continue
        qualified_count+=1
        success,status,details=send_email(email,"Proposition de transport & logistique – TRMB SERVICES",body);lead["email_status"]=status;lead["message_id"]=details.get("message_id","");lead["sender"]=details.get("sender","");lead["sent_folder"]=details.get("sent_folder","")
        if success:
            history[lead["source_url"] or lead["company_or_title"]]={"email":email,"company":lead["company"],"domain":d,"message_id":details.get("message_id",""),"sender":details.get("sender",""),"sent_at":now_iso()};known.update({email,lead["source_url"] or lead["company_or_title"],d} if d else {email,lead["source_url"] or lead["company_or_title"]});used_domains.add(d);sent+=1;print(f"✅ EMAIL SENT: {details.get('sender','')} -> {email} | {details.get('message_id','')}")
        else:
            email_failures+=1;print(f"❌ EMAIL FAILED: {email}: {status}")
    save_history(history)
    with open(os.path.join(OUT_DIR,"leads.json"),"w",encoding="utf-8") as f:json.dump(rows,f,ensure_ascii=False,indent=2)
    print(f"Searched {searched} queries; Found {len(rows)} buyer leads; qualified {qualified_count}; sent {sent} emails; SMTP failures {email_failures}; no email {no_email}")

if __name__=="__main__":main()
