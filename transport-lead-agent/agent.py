import csv, json, os, re, smtplib, ssl, time, imaplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import Request, urlopen

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(OUT_DIR, "contacted.json")
MAX_EMAILS = int(os.environ.get("MAX_EMAILS_PER_RUN", "5"))
PRESENTATION_URL = os.environ.get("PRESENTATION_URL", "https://trmb.ma/")
SMTP_RETRIES = int(os.environ.get("SMTP_RETRIES", "3"))

QUERIES = [
    '"recherche transporteur" Maroc entreprise -transporteur -logistique',
    '"besoin de transport" Maroc entreprise marchandises -transporteur',
    '"demande de devis" "transport" Maroc industriel',
    '"appel d offres" "transport" Maroc marchandises entreprise',
    '"sous-traitance" "transport" Maroc marchandises',
    '"location de véhicules avec chauffeur" Maroc entreprise',
    '"transport de marchandises" "besoin" Maroc entreprise',
    '"prestataire logistique" Maroc "demande" entreprise',
    '"transport quotidien" Maroc usine entreprise',
    '"livraison" Maroc entreprise "prestataire transport"',
    '"flotte" Maroc entreprise "transport" besoin',
    '"affrètement" Maroc "chargeur" entreprise',
]
POSITIVE_SIGNALS = {"recherche transporteur":40,"besoin de transport":40,"demande de devis":35,"appel d'offres":35,"appel d offres":35,"sous-traitance":30,"location de véhicules avec chauffeur":30,"transport de marchandises":20,"transport quotidien":25,"besoin":10,"marchandises":10,"chargeur":15,"logistique":8}
COMPETITOR_SIGNALS = {"commissionnaire de transport":45,"société de transport":35,"transporteur":25,"freight forwarder":30,"freight forwarding":30,"prestataire transport":25,"solution de transport":15,"flotte de camions":25,"camions disponibles":25,"bourse de fret":30}
GENERIC_EMAIL_PREFIXES = {"noreply","no-reply","donotreply","do-not-reply","mailer-daemon"}
GENERIC_PROVIDER_DOMAINS = {"linkedin.com","facebook.com","instagram.com","youtube.com","procurement.ma","datao.ma","kerix-export.net","kerix.net","google.com","gmail.com","yahoo.com","outlook.com","hotmail.com"}
CONTACT_PATHS = ("contact","contact-us","nous-contacter","a-propos","about","mentions-legales")

def now_iso(): return datetime.now(timezone.utc).isoformat()

def http_json(url,payload,headers):
    req=Request(url,data=json.dumps(payload).encode(),headers=headers,method="POST")
    try:
        with urlopen(req,timeout=45) as r: return json.loads(r.read().decode())
    except HTTPError as e: raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:500]}")
    except URLError as e: raise RuntimeError(f"Network error calling {url}: {e.reason}")

def tavily_search(query):
    if not TAVILY_KEY: raise RuntimeError("TAVILY_API_KEY is required")
    return http_json("https://api.tavily.com/search",{"query":query,"search_depth":"advanced","max_results":10,"include_answer":False},{"Content-Type":"application/json","Authorization":f"Bearer {TAVILY_KEY}"}).get("results",[])

def fetch_page(url):
    try:
        with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0 TRMB lead research bot"}),timeout=12) as r: raw=r.read(400000).decode("utf-8",errors="ignore")
        text=re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>"," ",raw,flags=re.I); text=re.sub(r"\s+"," ",text).strip()
        return text[:100000],re.findall(r'href=["\']([^"\']+)["\']',raw,flags=re.I)
    except Exception: return "",[]

def extract_emails(text):
    out=[]
    for e in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",text or ""):
        e=e.lower().strip(".,;:()[]<>")
        if e.split("@",1)[0] not in GENERIC_EMAIL_PREFIXES and e not in out: out.append(e)
    return out

def source_domain(url):
    try: return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception: return ""

def usable_email(email,url=""):
    if not email or "@" not in email: return False
    domain=email.split("@",1)[1].lower()
    return domain not in GENERIC_PROVIDER_DOMAINS and source_domain(url) not in GENERIC_PROVIDER_DOMAINS

def discover_contact_email(url):
    if not url: return ""
    _,links=fetch_page(url); candidates=[url]
    for link in links:
        if link.lower().startswith("mailto:"):
            for e in extract_emails(unquote(link[7:].split("?",1)[0])):
                if usable_email(e,url): return e
        absolute=urljoin(url,link); p=urlparse(absolute)
        if p.scheme in ("http","https"):
            path=p.path.lower().strip("/")
            if path in CONTACT_PATHS or any(path.endswith("/"+x) for x in CONTACT_PATHS): candidates.append(absolute)
    for candidate in list(dict.fromkeys(candidates))[:8]:
        text,plinks=fetch_page(candidate)
        for e in extract_emails(text):
            if usable_email(e,candidate): return e
        for link in plinks:
            if link.lower().startswith("mailto:"):
                for e in extract_emails(unquote(link[7:].split("?",1)[0])):
                    if usable_email(e,candidate): return e
    return ""

def tavily_find_public_email(title,url):
    domain=source_domain(url); target=f"site:{domain} contact email" if domain else f'"{title}" Maroc contact email'
    try: results=tavily_search(target)
    except RuntimeError as e: print(f"⚠️ Email lookup skipped: {e}"); return ""
    for item in results:
        for e in extract_emails(f"{item.get('title','')} {item.get('content','')}"):
            if usable_email(e,item.get("url","") or url): return e
    return ""

def score(text):
    t=text.lower(); p=sum(v for k,v in POSITIVE_SIGNALS.items() if k in t)-sum(v for k,v in COMPETITOR_SIGNALS.items() if k in t)
    if any(x in t for x in ("2026","2027","urgent","immédiat","immediate","en cours")): p+=10
    return max(0,min(p,100))

def looks_stale(text):
    years=[int(y) for y in re.findall(r"\b20\d{2}\b",text.lower())]
    return bool(years and max(years)<datetime.now(timezone.utc).year-1)

def qualify(title,content,url,email):
    text=f"{title} {content}"; s=score(text); stale=looks_stale(text); email=email or (extract_emails(text)[0] if extract_emails(text) else "")
    ok=s>=30 and bool(email) and usable_email(email,url) and not stale
    need=re.sub(r"\s+"," ",content).strip()[:500]
    body=("Bonjour,\n\nNous avons identifié un besoin potentiel en transport/logistique lié à votre activité. "
          "TRMB SERVICES peut vous accompagner avec des solutions de transport et de logistique adaptées à vos opérations au Maroc.\n\n"
          f"Besoin identifié : {need}\n\nPrésentation TRMB SERVICES : {PRESENTATION_URL}\n\n"
          "Si ce besoin est toujours d’actualité, nous pouvons vous transmettre rapidement une proposition adaptée.\n\n"
          "Bien cordialement,\nTRMB SERVICES\nhttps://trmb.ma/")
    return ok,email,f"score={s}; email public={'oui' if email else 'non'}; stale={'oui' if stale else 'non'}",body

def load_history():
    try:
        with open(HISTORY_FILE,encoding="utf-8") as f: d=json.load(f); return d if isinstance(d,dict) else {}
    except (FileNotFoundError,json.JSONDecodeError): return {}

def save_history(d):
    with open(HISTORY_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)

def history_keys(d):
    keys=set(d.keys())
    for v in d.values():
        if isinstance(v,dict) and v.get("email"): keys.add(v["email"].lower())
    return keys

def save_to_sent(raw):
    host=os.environ.get("IMAP_HOST",os.environ.get("SMTP_HOST","")).strip(); port=int(os.environ.get("IMAP_PORT","993")); user=os.environ.get("IMAP_USER",os.environ.get("SMTP_USER","")).strip(); password=os.environ.get("IMAP_PASSWORD",os.environ.get("SMTP_PASSWORD",""))
    if not all([host,user,password]): return False,"IMAP not configured"
    wanted=[x.strip() for x in os.environ.get("IMAP_SENT_FOLDER","Sent,INBOX.Sent,INBOX/Sent").split(",") if x.strip()]
    try:
        with imaplib.IMAP4_SSL(host,port) as imap:
            imap.login(user,password); status,names=imap.list(); available=[]
            if status=="OK":
                for raw_name in names or []:
                    line=raw_name.decode(errors="ignore"); m=re.search(r'"([^"]+)"\s*$',line)
                    if m: available.append(m.group(1))
            target=next((x for x in wanted if x in available),None)
            if not target: target=next((x for x in available if x.lower() in {"sent","sent items","inbox.sent","inbox/sent"}),None)
            if not target: return False,"Sent folder not found"
            ok,_=imap.append(target,"\\Seen",imaplib.Time2Internaldate(time.time()),raw)
            return ok=="OK",("saved to Sent" if ok=="OK" else "IMAP append failed")
    except Exception as e: return False,f"IMAP Sent save error: {e}"

def send_email(to_addr,subject,body):
    host=os.environ.get("SMTP_HOST","").strip(); port=int(os.environ.get("SMTP_PORT","465")); user=os.environ.get("SMTP_USER","").strip(); password=os.environ.get("SMTP_PASSWORD",""); sender=os.environ.get("SMTP_FROM","").strip() or user
    if not all([host,user,password,sender]): return False,"SMTP credentials not configured",{}
    msg=EmailMessage(); msg["From"]=sender; msg["To"]=to_addr; msg["Subject"]=subject; msg["Date"]=formatdate(localtime=False); msg["Message-ID"]=make_msgid(); msg["Reply-To"]=sender; msg.set_content(body); raw=msg.as_bytes()
    last="unknown SMTP error"
    for attempt in range(1,SMTP_RETRIES+1):
        try:
            refused={}
            if port==465:
                with smtplib.SMTP_SSL(host,port,timeout=30,context=ssl.create_default_context()) as server:
                    server.ehlo(); server.login(user,password); refused=server.send_message(msg,from_addr=sender,to_addrs=[to_addr])
            else:
                with smtplib.SMTP(host,port,timeout=30) as server:
                    server.ehlo(); server.starttls(context=ssl.create_default_context()); server.ehlo(); server.login(user,password); refused=server.send_message(msg,from_addr=sender,to_addrs=[to_addr])
            if refused: return False,f"SMTP refused recipient: {refused}",{"message_id":msg["Message-ID"],"sender":sender}
            saved,sstatus=save_to_sent(raw); details={"message_id":msg["Message-ID"],"sender":sender,"sent_folder":sstatus,"smtp_accepted":True}
            if saved: return True,"sent_and_saved_to_sent",details
            print(f"ℹ️ SMTP accepted {to_addr}; Sent copy: {sstatus}")
            return True,"sent_smtp_accepted",details
        except (smtplib.SMTPException,OSError) as e:
            last=f"SMTP error: {e}"
            if attempt<SMTP_RETRIES: time.sleep(2*attempt)
    return False,last,{"sender":sender}

def main():
    os.makedirs(OUT_DIR,exist_ok=True); history=load_history(); known=history_keys(history); leads={}
    print(f"Starting Morocco-only B2B discovery with {len(QUERIES)} Tavily queries...")
    for q in QUERIES:
        print(f"Searching: {q}")
        try: results=tavily_search(q)
        except RuntimeError as e: print(f"⚠️ Tavily query skipped: {e}"); continue
        for item in results:
            title=item.get("title","").strip(); url=item.get("url","").strip(); content=item.get("content",""); text=f"{title} {content}"; key=url or title.lower()
            emails=[e for e in extract_emails(text) if usable_email(e,url)]; email=emails[0] if emails else ""
            if not email and url: email=discover_contact_email(url)
            if not email: email=tavily_find_public_email(title,url)
            if not key or key in known or (email and email in known): continue
            if "maroc" not in text.lower() and ".ma" not in url.lower(): continue
            s=score(text)
            if s<30: continue
            leads[key]={"company_or_title":title,"need":re.sub(r"\s+"," ",content).strip()[:1500],"score":s,"source_url":url,"email_found":email,"country_hint":"Maroc","found_at":now_iso()}
    rows=sorted(leads.values(),key=lambda x:x["score"],reverse=True); sent=qualified_count=email_failures=no_email=0
    for lead in rows:
        if sent>=MAX_EMAILS: break
        qualified,email,reason,body=qualify(lead["company_or_title"],lead["need"],lead["source_url"],lead.get("email_found","")); lead.update({"qualified":qualified,"company":lead["company_or_title"],"email":email,"reason":reason})
        if not email: no_email+=1
        if not qualified: lead["email_status"]="not_sent_not_qualified"; continue
        qualified_count+=1; email=email.lower()
        if email in known: lead["email_status"]="not_sent_duplicate"; continue
        ok,status,details=send_email(email,"Proposition de transport & logistique – TRMB SERVICES",body); lead["email_status"]=status; lead["message_id"]=details.get("message_id",""); lead["sender"]=details.get("sender",""); lead["sent_folder"]=details.get("sent_folder","")
        if ok:
            history[lead["source_url"] or lead["company_or_title"]]={"email":email,"company":lead["company"],"message_id":details.get("message_id",""),"sender":details.get("sender",""),"sent_at":now_iso()}; known.update({email,lead["source_url"] or lead["company_or_title"]}); sent+=1; print(f"✅ EMAIL SENT: {details.get('sender','')} -> {email} | {details.get('message_id','')}")
        else: email_failures+=1; print(f"❌ EMAIL FAILED: {email}: {status}")
    save_history(history)
    with open(os.path.join(OUT_DIR,"leads.json"),"w",encoding="utf-8") as f: json.dump(rows,f,ensure_ascii=False,indent=2)
    with open(os.path.join(OUT_DIR,"leads.csv"),"w",encoding="utf-8",newline="") as f:
        fields=["company_or_title","company","email","qualified","score","email_status","message_id","sender","sent_folder","reason","need","source_url","country_hint","found_at"]; w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print(f"Found {len(rows)} leads; qualified {qualified_count}; sent {sent} emails; SMTP failures {email_failures}; no email {no_email}")
    if qualified_count and sent==0: raise RuntimeError(f"No qualified prospect was contacted: qualified={qualified_count}, SMTP failures={email_failures}")
    if email_failures: raise RuntimeError(f"Commercial email sending failures detected: {email_failures}")

if __name__=="__main__": main()
