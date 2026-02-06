# scripts/scrape_mercari.py
import json, re, time, random
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("data/sold.json")
DBG_DIR = Path("debug")
OUT.parent.mkdir(parents=True, exist_ok=True)
DBG_DIR.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
    "ポケパークカントー ピンバッジ",
    "pokemon park kanto pin",
]

# 你要抓的格式：標題含「No. 0138 オムナイト」這種
RE_NO = re.compile(r"\bNo\.?\s*0*(\d{1,4})\b", re.IGNORECASE)

# 常見：No. 0138 オムナイト / No.0138 オムナイト
def parse_no_and_name(title: str):
    m = RE_NO.search(title or "")
    if not m:
        return None, None
    no = int(m.group(1))
    # 嘗試取 No 後面第一段文字當寶可夢名（非常保守）
    tail = title[m.end():].strip()
    # 去掉多餘符號
    tail = re.sub(r"^[\-\:\｜\|\／/]+", "", tail).strip()
    # 取第一段（遇到空白/括號/【】就切）
    name = re.split(r"[\s\(\)（）。．【】\[\]｜\|/／:：\-–—]+", tail)[0].strip()
    if not name:
        name = None
    return no, name

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def safe_filename(s: str):
    s = s.strip().replace(" ", "_")
    s = re.sub(r"[^\w\u3000-\u30ff\u4e00-\u9fff\-\_]+", "_", s)
    return s[:120] or "debug"

def scrape_one_keyword(page, keyword: str):
    # Mercari JP 搜尋（注意：Mercari 參數可能會變，先用最穩的 keyword）
    url = "https://jp.mercari.com/search?keyword=" + __import__("urllib.parse").parse.quote(keyword)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    # 讓 JS 有時間把卡片渲染出來（保守做法：等一下+嘗試等待某些元素）
    page.wait_for_timeout(2500)

    # 滾動幾次載入更多
    for _ in range(3):
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(1200)

    html = page.content()
    dbg_path = DBG_DIR / f"search_{safe_filename(keyword)}.html"
    dbg_path.write_text(html, encoding="utf-8")

    items = []

    # 嘗試抓商品卡片連結（多種 selector fallback）
    # Mercari 版型會變，所以這裡做「盡量抓到 href 像 /item/… 的連結」
    links = page.locator('a[href^="/item/"]').all()
    seen = set()

    for a in links:
        try:
            href = a.get_attribute("href") or ""
            if not href.startswith("/item/"):
                continue
            if href in seen:
                continue
            seen.add(href)

            # 嘗試從卡片附近拿 title/price（向上找最近可見文字）
            # 這段是保守抓法：拿 a 的 inner_text + 父層 text
            title = (a.inner_text() or "").strip()
            container_text = (a.locator("xpath=ancestor::*[1]").inner_text() or "").strip()
            blob = (title + "\n" + container_text).strip()

            # 先從 blob 裡找像「¥12,345」的價格
            m_price = re.search(r"¥\s*([\d,]+)", blob)
            price = int(m_price.group(1).replace(",", "")) if m_price else None

            # 如果還是沒價錢，再往上找大一層
            if price is None:
                up = a.locator("xpath=ancestor::*[2]")
                blob2 = (up.inner_text() or "").strip()
                m_price = re.search(r"¥\s*([\d,]+)", blob2)
                price = int(m_price.group(1).replace(",", "")) if m_price else None
                if not title:
                    title = blob2.split("\n")[0].strip()

            # 最少要有標題與價格才收
            if not title:
                continue
            if price is None:
                continue

            no, pname = parse_no_and_name(title)
            # 你要的是「有標示 151 編號 + 寶可夢名」的賣場：沒 No 的就略過
            if not no:
                continue

            item_url = "https://jp.mercari.com" + href

            items.append({
                "keyword": keyword,
                "title": title,
                "price_jpy": price,
                "sold_at": now_iso(),   # 這裡先用抓取時間（要更精準需進 item 頁抓售出時間）
                "item_url": item_url,
                "no": no,
                "pokemon_name": pname or "",
            })

        except Exception:
            continue

    return {
        "keyword": keyword,
        "url": url,
        "status": 200,
        "debug_html": str(dbg_path),
        "found": len(items),
        "items": items,
    }

def main():
    all_items = []
    debug = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # 假裝正常瀏覽器
        page.set_extra_http_headers({
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        })

        for kw in KEYWORDS:
            # 低頻率 + 隨機延遲
            time.sleep(random.uniform(1.2, 2.4))
            r = scrape_one_keyword(page, kw)
            debug.append({k: r[k] for k in ["keyword","url","status","debug_html","found"]})
            all_items.extend(r["items"])

        browser.close()

    payload = {
        "updated_at": now_iso(),
        "count": len(all_items),
        "items": all_items,
        "debug": debug,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
