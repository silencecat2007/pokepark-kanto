import json, re, time, random
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

OUT = Path("data/sold.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
    "pokemon park kanto pin",
    "ポケパークカントー ピンズ ピンバッチ",
]

# 低頻率抓取，避免造成負擔/觸發防護
SLEEP_BETWEEN_PAGES = (1.2, 2.4)
SLEEP_BETWEEN_ITEMS = (0.9, 1.8)
MAX_SEARCH_PAGES = 3          # 每個關鍵字最多翻幾頁（先保守）
MAX_ITEMS_PER_KEYWORD = 80    # 每個關鍵字最多處理幾個商品頁

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36"

NO_RE = re.compile(r"(?:No\.?\s*|NO\.?\s*)(\d{1,4})", re.IGNORECASE)

def jitter(a_b):
    a, b = a_b
    time.sleep(random.uniform(a, b))

def get(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.text

def parse_search_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    # Mercari 搜尋頁通常會有 a[href*="/items/"] 或 a[href*="/item/"]
    for a in soup.select('a[href]'):
        href = a.get("href") or ""
        if "/items/" in href or "/item/" in href:
            if href.startswith("/"):
                href = "https://tw.mercari.com" + href
            if href.startswith("https://tw.mercari.com/"):
                links.add(href.split("?")[0])
    return list(links)

def parse_item_page(url, html, keyword):
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.find("h1").get_text(strip=True) if soup.find("h1") else "").strip()
    if not title:
        # 後備：title tag
        t = soup.find("title")
        title = t.get_text(strip=True) if t else ""

    m = NO_RE.search(title)
    if not m:
        return None
    no = int(m.group(1))
    if no < 1 or no > 151:
        return None

    text = soup.get_text("\n", strip=True)

    # sold 判斷：頁面會出現 sold / 售完 / 已售出 等字樣
    sold = ("sold" in text.lower()) or ("售完" in text) or ("已售出" in text)
    if not sold:
        return None

    # 價格：優先抓 (JP¥x,xxx) 形式
    price_jpy = None
    jpy_m = re.search(r"\(JP¥\s*([\d,]+)\)", text)
    if jpy_m:
        price_jpy = int(jpy_m.group(1).replace(",", ""))
    else:
        # 後備：直接找 JP¥xxxx
        jpy_m2 = re.search(r"JP¥\s*([\d,]+)", text)
        if jpy_m2:
            price_jpy = int(jpy_m2.group(1).replace(",", ""))

    if price_jpy is None:
        return None

    # sold_at：Mercari 顯示可能是「1 天前」這種相對時間；先用抓取時間當 sold_at（可接受）
    sold_at = datetime.now(timezone.utc).isoformat()

    # 嘗試從標題擷取寶可夢名：通常 No. #### 後面會跟日文名/中文名
    name = ""
    # e.g. "... No. 0138 オムナイト" / "... No. 0138 菊石獸"
    after = title[m.end():].strip()
    # 清掉常見字
    after = re.sub(r"^[\-\—\:\｜\|]+", "", after).strip()
    # 取第一段像名字的 token
    if after:
        name = after.split()[0].strip()

    return {
        "keyword": keyword,
        "item_url": url,
        "title": title,
        "no": no,
        "pokemon_name": name,
        "price_jpy": price_jpy,
        "sold_at": sold_at,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

def main():
    all_items = []
    for kw in KEYWORDS:
        seen = set()
        collected = 0
        for page in range(1, MAX_SEARCH_PAGES + 1):
            q = requests.utils.quote(kw)
            # Mercari 搜尋：keyword + page
            #（參考：搜尋參數整理） https://jp.mercari.com/search?keyword=...
            url = f"https://tw.mercari.com/zh-hant/search?keyword={q}&page={page}"
            html = get(url)
            links = parse_search_links(html)

            for link in links:
                if link in seen:
                    continue
                seen.add(link)
                try:
                    item_html = get(link)
                    it = parse_item_page(link, item_html, kw)
                    if it:
                        all_items.append(it)
                        collected += 1
                except Exception:
                    pass

                jitter(SLEEP_BETWEEN_ITEMS)
                if collected >= MAX_ITEMS_PER_KEYWORD:
                    break

            jitter(SLEEP_BETWEEN_PAGES)
            if collected >= MAX_ITEMS_PER_KEYWORD:
                break

    # 去重：同一個 item_url 保留最新
    dedup = {}
    for it in all_items:
        dedup[it["item_url"]] = it
    items = list(dedup.values())

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": items
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} with {len(items)} items")

if __name__ == "__main__":
    main()
