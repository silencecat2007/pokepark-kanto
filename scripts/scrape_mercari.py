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

# 你要的關鍵字（不含編號）
KEYWORDS = [
    "pokemon park kanto pin",
    "ポケパークカントー ピンバッジ",
]

# 同時抓：已售出 + 交易中
STATUSES = ["sold_out", "on_sale"]

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sleep_small():
    time.sleep(random.uniform(0.6, 1.2))

def quote(s: str) -> str:
    from urllib.parse import quote as _q
    return _q(s, safe="")

def build_search_url(keyword: str, status: str) -> str:
    # Mercari JP 常見：status=sold_out / on_sale
    return f"https://jp.mercari.com/search?keyword={quote(keyword)}&status={status}"

def text_to_jpy(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.replace(",", "").replace("￥", "¥")
    m = re.search(r"¥\s*(\d+)", t)
    return int(m.group(1)) if m else None

def load_or_fetch_pokemon_ja() -> List[Dict]:
    """
    取得 1~151 的日文名（ja-Hrkt）。
    會快取到 data/pokemon_151_ja.json，之後 Actions 跑更快。
    """
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

        ja = None
        for n in data.get("names", []):
            lang = n.get("language", {}).get("name")
            if lang == "ja-Hrkt":
                ja = n.get("name")
                break

        if not ja:
            # fallback：有些情況至少抓 ja
            for n in data.get("names", []):
                lang = n.get("language", {}).get("name")
                if lang == "ja":
                    ja = n.get("name")
                    break

        out.append({"no": no, "ja": ja or ""})
        time.sleep(0.05)  # 對 PokeAPI 友善一點

    POKE_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

def make_name_matcher(poke_ja: List[Dict]) -> Tuple[re.Pattern, Dict[str, int]]:
    """
    建立「名稱大集合 regex」來快速從標題判定是哪隻。
    會用最長優先，避免短字撞到長字。
    """
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
    # 用 re.escape 以免名字包含特殊字
    pat = re.compile("|".join(re.escape(n) for n in names))
    return pat, name_to_no

def extract_title_and_price_from_card(container) -> Tuple[Optional[str], Optional[int]]:
    title = None
    price = None

    img = container.query_selector("img[alt]")
    if img:
        title = img.get_attribute("alt")

    if not title:
        txt = container.inner_text().strip()
        title = txt.split("\n")[0][:200] if txt else None

    t = container.inner_text()
    price = text_to_jpy(t)

    return title, price

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

        for kw in KEYWORDS:
            for st in STATUSES:
                url = build_search_url(kw, st)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # 等到至少有一個商品連結出現（/item/）
                try:
                    page.wait_for_selector("a[href^='/item/']", timeout=25000)
                except Exception:
                    debug.append({
                        "keyword": kw,
                        "status": st,
                        "url": url,
                        "note": "no item links found (maybe blocked / empty / DOM changed)"
                    })
                    continue

                max_collect = 160
                collected = set()

                # 滾動載入更多
                for _ in range(10):
                    links = page.query_selector_all("a[href^='/item/']")
                    for a in links:
                        href = a.get_attribute("href") or ""
                        if not href.startswith("/item/"):
                            continue
                        collected.add("https://jp.mercari.com" + href)
                        if len(collected) >= max_collect:
                            break
                    if len(collected) >= max_collect:
                        break
                    page.mouse.wheel(0, 1700)
                    sleep_small()

                matched = 0
                for item_url in list(collected):
                    # 找回該 link 對應的 card
                    a = page.query_selector(f"a[href='{item_url.replace('https://jp.mercari.com','')}']")
                    if not a:
                        continue

                    container = a.evaluate_handle(
                        "(el) => el.closest('li') || el.closest('div') || el.parentElement"
                    )
                    if not container:
                        continue

                    title, price = extract_title_and_price_from_card(container)
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
                    items.append({
                        "keyword": kw,
                        "listing_status": st,      # sold_out / on_sale
                        "no": no,                  # 1~151
                        "pokemon_name": pkm_name,  # 日文名（ja-Hrkt）
                        "title": title,
                        "price_jpy": price,
                        "item_url": item_url,
                        "scraped_at": now_iso(),
                    })

                debug.append({
                    "keyword": kw,
                    "status": st,
                    "url": url,
                    "collected_links": len(collected),
                    "matched_items": matched
                })

                sleep_small()

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
