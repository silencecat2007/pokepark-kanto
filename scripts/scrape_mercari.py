import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from playwright.sync_api import sync_playwright

OUT = Path("data/sold.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

POKE_CACHE = Path("data/pokemon_151_ja.json")
POKE_CACHE.parent.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
    "pokemon park kanto pin",
    "ポケパークカントー ピンバッジ",
]

STATUSES = ["sold_out", "on_sale"]

MAX_LINKS_PER_SEARCH = 80        # 每個搜尋頁最多拿幾個 item 連結
MAX_ITEM_PAGES_TOTAL = 120       # 全部搜尋合計最多打開幾個 item 頁（避免跑太久）

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sleep_small(a=0.6, b=1.2):
    time.sleep(random.uniform(a, b))

def quote(s: str) -> str:
    from urllib.parse import quote as _q
    return _q(s, safe="")

def build_search_url(keyword: str, status: str) -> str:
    return f"https://jp.mercari.com/search?keyword={quote(keyword)}&status={status}"

def text_to_jpy(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.replace(",", "").replace("￥", "¥")
    m = re.search(r"¥\s*(\d+)", t)
    return int(m.group(1)) if m else None

def load_or_fetch_pokemon_ja() -> List[Dict]:
    """抓 1~151 日文名（ja-Hrkt）並快取。"""
    if POKE_CACHE.exists():
        return json.loads(POKE_CACHE.read_text(encoding="utf-8"))

    out = []
    s = requests.Session()
    s.headers.update({"User-Agent": "pokepark-kanto-scraper/1.0"})

    for no in range(1, 152):
        url = f"https://pokeapi.co/api/v2/pokemon-species/{no}/"
        r = s.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()

        ja = ""
        for n in data.get("names", []):
            if n.get("language", {}).get("name") == "ja-Hrkt":
                ja = n.get("name", "")
                break
        if not ja:
            for n in data.get("names", []):
                if n.get("language", {}).get("name") == "ja":
                    ja = n.get("name", "")
                    break

        out.append({"no": no, "ja": ja})
        time.sleep(0.05)

    POKE_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

def make_name_matcher(poke_ja: List[Dict]) -> Tuple[re.Pattern, Dict[str, int]]:
    """建立最長優先的名稱 regex，避免短字撞長字。"""
    name_to_no = {}
    names = []
    for row in poke_ja:
        name = (row.get("ja") or "").strip()
        no = int(row.get("no"))
        if not name:
            continue
        name_to_no[name] = no
        names.append(name)

    names.sort(key=len, reverse=True)
    pat = re.compile("|".join(re.escape(n) for n in names))
    return pat, name_to_no

def collect_item_links(page) -> List[str]:
    """從搜尋頁收集 /item/ 連結（滾動載入）。"""
    collected = set()

    # 等到有 item 連結
    page.wait_for_selector("a[href^='/item/']", timeout=25000)

    for _ in range(10):
        links = page.query_selector_all("a[href^='/item/']")
        for a in links:
            href = a.get_attribute("href") or ""
            if href.startswith("/item/"):
                collected.add("https://jp.mercari.com" + href)
            if len(collected) >= MAX_LINKS_PER_SEARCH:
                break
        if len(collected) >= MAX_LINKS_PER_SEARCH:
            break
        page.mouse.wheel(0, 1800)
        sleep_small()

    return list(collected)

def extract_item_detail(page) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """
    從 item 頁抓：標題 / 價格 / 狀態文字（賣切等）
    （選擇器可能會變，所以用多段 fallback）
    """
    # title
    title = None
    h1 = page.query_selector("h1")
    if h1:
        t = (h1.inner_text() or "").strip()
        if t:
            title = t

    if not title:
        # fallback：找 meta
        mt = page.query_selector("meta[property='og:title']")
        if mt:
            title = (mt.get_attribute("content") or "").strip() or None

    body_text = (page.inner_text("body") or "")

    # price
    price = text_to_jpy(body_text)

    # status hint
    status_hint = None
    # 常見：売り切れ / SOLD / 取引中 等
    for k in ["売り切れ", "SOLD", "取引中", "販売中"]:
        if k in body_text:
            status_hint = k
            break

    return title, price, status_hint

def main():
    poke_ja = load_or_fetch_pokemon_ja()
    name_re, name_to_no = make_name_matcher(poke_ja)

    items = []
    debug = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        seen_item_urls = set()
        visited_total = 0

        for kw in KEYWORDS:
            for st in STATUSES:
                url = build_search_url(kw, st)

                collected_links = 0
                visited = 0
                matched = 0
                errors = 0

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    links = collect_item_links(page)
                    collected_links = len(links)
                except Exception as e:
                    debug.append({
                        "keyword": kw,
                        "status": st,
                        "url": url,
                        "collected_links": 0,
                        "visited_items": 0,
                        "matched_items": 0,
                        "errors": 1,
                        "note": f"search page failed: {type(e).__name__}"
                    })
                    continue

                # 逐一打開 item 頁抓標題/價格
                for item_url in links:
                    if item_url in seen_item_urls:
                        continue
                    seen_item_urls.add(item_url)

                    if visited_total >= MAX_ITEM_PAGES_TOTAL:
                        break

                    try:
                        visited_total += 1
                        visited += 1

                        page.goto(item_url, wait_until="domcontentloaded", timeout=60000)
                        sleep_small(0.3, 0.6)

                        title, price, status_hint = extract_item_detail(page)
                        if not title:
                            continue

                        m = name_re.search(title)
                        if not m:
                            continue  # 標題沒含 151 任何一隻日文名，就跳過

                        pkm_name = m.group(0)
                        no = name_to_no.get(pkm_name)
                        if not no:
                            continue

                        matched += 1
                        t = now_iso()

                        # UI 需要 sold_at 才能畫圖：這裡用 scraped_at 代替（至少能追蹤趨勢）
                        items.append({
                            "keyword": kw,
                            "listing_status": st,        # sold_out / on_sale
                            "status_hint": status_hint,  # 取引中/売り切れ等（可選）
                            "no": no,
                            "pokemon_name": pkm_name,
                            "title": title,
                            "price_jpy": price,
                            "item_url": item_url,
                            "sold_at": t,                # 用抓取時間當作時間序列點
                            "scraped_at": t,
                        })

                    except Exception:
                        errors += 1
                        continue

                    sleep_small(0.5, 1.1)

                debug.append({
                    "keyword": kw,
                    "status": st,
                    "url": url,
                    "collected_links": collected_links,
                    "visited_items": visited,
                    "matched_items": matched,
                    "errors": errors
                })

                sleep_small(0.6, 1.2)

        context.close()
        browser.close()

    payload = {
        "updated_at": now_iso(),
        "count": len(items),
        "items": items,
        "debug": debug,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
