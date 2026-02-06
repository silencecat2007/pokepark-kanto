import json, re, time, random
from datetime import datetime, timezone
from pathlib import Path
import requests

OUT = Path("data/sold.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
    "ポケパークカントー ピンバッジ"
]

SLEEP = (1.0, 2.0)
TIMEOUT = 20

def now():
    return datetime.now(timezone.utc).isoformat()

def sleep():
    time.sleep(random.uniform(*SLEEP))

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ja,en;q=0.9"
    })
    return s

def extract_next_data(html):
    m = re.search(r'id="__NEXT_DATA__".*?>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except:
        return None

def find_items(obj):
    found = []
    if isinstance(obj, dict):
        if "price" in obj and ("name" in obj or "title" in obj):
            found.append(obj)
        for v in obj.values():
            found += find_items(v)
    elif isinstance(obj, list):
        for v in obj:
            found += find_items(v)
    return found

def is_sold(d, html):
    if d.get("isSoldOut") or d.get("sold"):
        return True
    return "売り切れ" in html or "SOLD" in html

def get_price(d):
    p = d.get("price")
    if isinstance(p, int):
        return p
    if isinstance(p, str):
        p = re.sub(r"[^\d]", "", p)
        return int(p) if p else None
    return None

def get_title(d):
    return d.get("name") or d.get("title") or ""

def get_url(d):
    if "id" in d:
        return f"https://jp.mercari.com/item/{d['id']}"
    return None

def extract_pokemon_name(title):
    # 非強制，只是輔助分類
    names = [
        "フシギダネ","フシギソウ","フシギバナ","ヒトカゲ","リザードン",
        "ゼニガメ","ピカチュウ","イーブイ"
    ]
    for n in names:
        if n in title:
            return n
    return "unknown"

def main():
    s = make_session()
    items = []

    for kw in KEYWORDS:
        for page in range(1, 6):
            url = f"https://jp.mercari.com/search?keyword={kw}&page={page}"
            try:
                html = s.get(url, timeout=TIMEOUT).text
            except:
                continue

            nd = extract_next_data(html)
