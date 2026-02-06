import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# ===== 設定 =====
KEYWORDS = [
    "ポケパークカントー ピンバッジ",
    # 你也可以加第二組英文，但先用你指定的這組就好
    # "pokemon park kanto pin",
]

# 每次抓多少個「商品連結」去判斷是否已售出（越大越慢）
MAX_ITEM_LINKS = 80

# 搜尋頁最多往下滾幾次（越大越多結果）
MAX_SCROLLS = 12

OUT = Path("data/sold.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

# Mercari 可能會跳地區站：jp.mercari.com / tw.mercari.com 都試
SEARCH_URLS = [
    "https://jp.mercari.com/search?keyword={q}",
    "https://tw.mercari.com/zh-hant/search?keyword={q}",
]

# 判斷「已售出」的關鍵字（頁面可能是日文/英文/中文）
SOLD_HINTS = [
    "SOLD",
    "売り切れ",
    "販売終了",
    "已售出",
    "售出",
]

# 從標題抓編號與寶可夢名（盡量寬鬆）
RE_NO = re.compile(r"(?:No\.?|NO\.?|№)\s*0*([0-9]{1,3})", re.IGNORECASE)

def now_taipei_iso():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")

def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def extract_no_and_name(title: str):
    """回傳 (no_int_or_None, name_guess_or_None)"""
    t = normalize_space(title)
    m = RE_NO.search(t)
    if not m:
        return None, None
    no = int(m.group(1))
    # 嘗試從 No. 之後抓名字（例：No. 0138 オムナイト）
    after = t[m.end():].strip(" ：:・-—|()[]　")
    # 只取前面一小段，避免把多餘描述一起吃進來
    name = after.split(" ")[0] if after else None
    name = name if name else None
    return no, name

def parse_price(text: str):
    """抓到像 '¥12,800' / '￥9800' / 'NT$ 300' 之類就回傳 (currency, amount_int)"""
    t = normalize_space(text)
    # JPY
    m = re.search(r"[¥￥]\s*([0-9][0-9,]*)", t)
    if m:
        return "JPY", int(m.group(1).replace(",", ""))
    # TWD / NT$
    m = re.search(r"(?:NT\$|NT＄|NT)\s*([0-9][0-9,]*)", t, re.IGNORECASE)
    if m:
        return "TWD", int(m.group(1).replace(",", ""))
    return None, None

def is_sold(page_text: str) -> bool:
    t = page_text
    return any(h in t for h in SOLD_HINTS)

def collect_item_links(page):
    """
    從搜尋頁抓商品連結。
    Mercari 站點 DOM 可能變動，所以這裡用「包含 /item/」的 href 來抓。
    """
    hrefs = set()
    anchors = page.locator("a").all()
    for a in anchors:
        try:
            href = a.get_attribute("href")
        except Exception:
            continue
        if not href:
            continue
        if "/item/" in href:
            # 補全
            if href.startswith("http"):
                hrefs.add(href)
            else:
                hrefs.add(page.url.split("/search")[0] + href)
    return list(hrefs)

def main():
    results = []
    run_meta = {
        "updated_at": now_taipei_iso(),
        "keywords": KEYWORDS,
        "count": 0,
        "items": results,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="ja-JP",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        )
        page = context.new_page()

        for kw in KEYWORDS:
            q = kw
            q_enc = re.sub(r" ", "%20", q)

            links = []
            for tpl in SEARCH_URLS:
                search_url = tpl.format(q=q_enc)
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    # 稍等一下 JS 載入
                    page.wait_for_timeout(1500)

                    # 捲動載入更多
                    for _ in range(MAX_SCROLLS):
                        page.mouse.wheel(0, 2000)
                        page.wait_for_timeout(800)

                    links = collect_item_links(page)
                    if links:
                        break
                except PWTimeoutError:
                    continue

            # 去重 + 截斷
            links = list(dict.fromkeys(links))[:MAX_ITEM_LINKS]

            for url in links:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(800)

                    text = page.content()  # 用 HTML 文字判斷 sold
                    if not is_sold(text):
                        continue

                    # 標題：優先 og:title
                    title = None
                    try:
                        title = page.locator("meta[property='og:title']").get_attribute("content")
                    except Exception:
                        pass
                    if not title:
                        title = page.title()

                    title = normalize_space(title or "")
                    if not title:
                        continue

                    # 價格：從頁面文字找第一個看起來像價格的片段
                    currency, amount = None, None
                    body_text = page.inner_text("body")
                    currency, amount = parse_price(body_text)

                    no, name_guess = extract_no_and_name(title)

                    results.append({
                        "keyword": kw,
                        "url": url,
                        "title": title,
                        "no": no,
                        "pokemon_name_guess": name_guess,
                        "currency": currency,
                        "price": amount,
                        "captured_at": now_taipei_iso(),
                    })

                except PWTimeoutError:
                    continue
                except Exception:
                    continue

        browser.close()

    run_meta["count"] = len(results)

    OUT.write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} items -> {OUT}")

if __name__ == "__main__":
    main()
