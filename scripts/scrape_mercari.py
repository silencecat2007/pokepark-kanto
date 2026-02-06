import json, re, time, random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

OUT = Path("data/sold.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
    "pokemon park kanto pin",
    "ポケパークカントー ピンズ ピンバッジ",
]

MAX_PAGES_PER_KEYWORD = 5
SLEEP_BETWEEN_REQ = (1.0, 2.2)
TIMEOUT = 25

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

SOLD_MARKERS = [
    "SOLD", "売り切れ", "取引完了", "売却済み"
]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def pad4(n: int) -> str:
    return str(n).zfill(4)

def extract_no(title: str) -> Optional[int]:
    if not title:
        return None
    m = re.search(r"(?:No\.?\s*|#)\s*(\d{1,4})", title, re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 151 else None

def parse_price_jpy_from_any(x: Any) -> Optional[int]:
    # 可能是 int, str("12345"), str("¥12,345"), "12,345円"
    if x is None:
        return None
    if isinstance(x, (int, float)):
        v = int(x)
        return v if v > 0 else None
    if isinstance(x, str):
        s = x.strip()
        m = re.search(r"[¥￥]\s*([\d,]+)", s)
        if not m:
            m = re.search(r"([\d,]+)\s*円", s)
        if not m and re.fullmatch(r"[\d,]+", s):
            m = re.match(r"([\d,]+)", s)
        if not m:
            return None
        return int(m.group(1).replace(",", ""))
    return None

def jitter():
    time.sleep(random.uniform(*SLEEP_BETWEEN_REQ))

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(UA_LIST),
        "Accept-Language": "ja,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s

def get_html(s: requests.Session, url: str) -> str:
    r = s.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def extract_next_data_json(html: str) -> Optional[Dict[str, Any]]:
    # Next.js: <script id="__NEXT_DATA__" type="application/json">...</script>
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        return None

def walk_find_dicts(obj: Any, want_keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    found = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if all(k in cur for k in want_keys):
                found.append(cur)
            for v in cur.values():
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return found

def normalize_item_url(path_or_url: str) -> Optional[str]:
    if not path_or_url:
        return None
    u = path_or_url.strip()
    if u.startswith("http"):
        return u.split("?")[0]
    if u.startswith("/"):
        return ("https://jp.mercari.com" + u).split("?")[0]
    return None

def is_sold_from_fields(d: Dict[str, Any]) -> bool:
    # 常見欄位：status / itemStatus / item_status / sold / isSoldOut 等
    for k in ("sold", "isSoldOut", "is_sold_out"):
        if k in d and isinstance(d[k], bool) and d[k]:
            return True
    for k in ("status", "itemStatus", "item_status", "transactionStatus", "transaction_status"):
        v = d.get(k)
        if isinstance(v, str):
            vv = v.lower()
            if any(x in vv for x in ["sold", "soldout", "sold_out", "completed", "trading", "finish", "finished"]):
                return True
    return False

def has_sold_marker_text(text: str) -> bool:
    if not text:
        return False
    return any(mk in text for mk in SOLD_MARKERS)

def parse_search_items_from_next_data(next_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    針對搜尋頁：盡量從 __NEXT_DATA__ 中找出 item-like dict
    常見會有 name/title, id, price, status 等
    """
    candidates = []
    # 以較寬鬆條件找 "id"+"name" 或 "id"+"title"
    candidates.extend(walk_find_dicts(next_data, ("id", "name")))
    candidates.extend(walk_find_dicts(next_data, ("id", "title")))
    # 去重（用 id）
    seen = set()
    out = []
    for d in candidates:
        iid = d.get("id")
        if iid is None:
            continue
        key = str(iid)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out

def parse_item_from_item_page(next_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    item page 會有更完整的 item 物件
    我們用最常見的特徵：包含 name/title + price + status 之類
    """
    # 先找包含 price 的 dict
    dicts = walk_find_dicts(next_data, ("price",))
    # 從中挑有 name/title 的
    best = None
    for d in dicts:
        if ("name" in d or "title" in d) and ("id" in d or "itemId" in d or "item_id" in d):
            best = d
            break
    if best:
        return best

    # 退而求其次：直接找 props.pageProps.item
    cur = next_data
    for k in ("props", "pageProps", "item"):
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            cur = None
            break
    if isinstance(cur, dict):
        return cur
    return None

def extract_link_paths_from_html(html: str) -> List[str]:
    # fallback：從 HTML 抓 /item/xxxxx 連結
    links = set(re.findall(r'href="(/item/[^"?]+)"', html))
    return ["https://jp.mercari.com" + p for p in sorted(links)]

def pick_title(d: Dict[str, Any]) -> str:
    for k in ("name", "title"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def pick_price(d: Dict[str, Any]) -> Optional[int]:
    # price 可能在 price / priceValue / itemPrice 等
    for k in ("price", "priceValue", "itemPrice", "item_price"):
        if k in d:
            p = parse_price_jpy_from_any(d.get(k))
            if p is not None:
                return p
    # 有些會在 "price" dict 裡
    if isinstance(d.get("price"), dict):
        for kk in ("value", "amount"):
            p = parse_price_jpy_from_any(d["price"].get(kk))
            if p is not None:
                return p
    return None

def pick_url(d: Dict[str, Any]) -> Optional[str]:
    for k in ("path", "url", "webUrl", "web_url", "link"):
        v = d.get(k)
        if isinstance(v, str):
            u = normalize_item_url(v)
            if u:
                return u
    # 有些只給 id，就用 /item/{id}
    iid = d.get("id") or d.get("itemId") or d.get("item_id")
    if isinstance(iid, str) and iid:
        return f"https://jp.mercari.com/item/{iid}"
    return None

def fetch_search_and_collect(s: requests.Session, keyword: str) -> List[str]:
    """
    回傳 item_url 清單（從搜尋頁 next_data 或 HTML fallback 抽出來）
    """
    item_urls: List[str] = []
    q = requests.utils.quote(keyword)

    for page in range(1, MAX_PAGES_PER_KEYWORD + 1):
        url = f"https://jp.mercari.com/search?keyword={q}&page={page}"
        try:
            html = get_html(s, url)
        except Exception as e:
            print(f"[ERR] search fetch: {url} -> {e}")
            continue

        nd = extract_next_data_json(html)
        if nd:
            items = parse_search_items_from_next_data(nd)
            got = 0
            for it in items:
                u = pick_url(it)
                if u and "/item/" in u:
                    item_urls.append(u)
                    got += 1
            print(f"[OK] search next_data: kw='{keyword}' page={page} items={got}")
        else:
            links = extract_link_paths_from_html(html)
            print(f"[WARN] search no next_data: kw='{keyword}' page={page} links={len(links)}")
            item_urls.extend(links)

        jitter()

    # unique keep order
    seen = set()
    uniq = []
    for u in item_urls:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return uniq

def main():
    s = make_session()

    results = []
    seen_item = set()

    for kw in KEYWORDS:
        item_urls = fetch_search_and_collect(s, kw)
        print(f"[INFO] kw='{kw}' total_item_urls={len(item_urls)}")

        for item_url in item_urls:
            if item_url in seen_item:
                continue
            seen_item.add(item_url)

            jitter()
            try:
                html = get_html(s, item_url)
            except Exception as e:
                print(f"[ERR] item fetch: {item_url} -> {e}")
                continue

            title = ""
            sold = False
            price = None

            nd = extract_next_data_json(html)
            if nd:
                item = parse_item_from_item_page(nd)
                if isinstance(item, dict):
                    title = pick_title(item)
                    price = pick_price(item)
                    sold = is_sold_from_fields(item)
            # 最後用頁面文字補 sold 判斷（保險）
            if not sold:
                sold = has_sold_marker_text(html)

            no = extract_no(title)
            if not no:
                continue
            if not sold:
                continue
            if price is None:
                # 有些頁面會把價格放在 meta/文字，但不在 next_data，簡單補抓
                price = parse_price_jpy_from_any(html)
            if price is None:
                continue

            results.append({
                "no": no,
                "title": title,
                "price_jpy": int(price),
                "item_url": item_url,
                "keyword": kw,
                "fetched_at": now_iso(),
            })

    # 排序：編號 -> 時間
    results.sort(key=lambda x: (x["no"], x["fetched_at"]))

    out = {
        "updated_at": now_iso(),
        "count": len(results),
        "items": results,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] wrote {OUT} count={len(results)}")

if __name__ == "__main__":
    main()
