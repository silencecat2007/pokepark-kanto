import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# ========= 你指定的搜尋關鍵字（不含編號） =========
KEYWORDS = ["ポケパークカントー ピンバッジ"]

# 抓多少個商品連結去判斷 SOLD（越大越慢）
MAX_ITEM_LINKS = 80

# 搜尋頁最多往下滾幾次（越大結果越多）
MAX_SCROLLS = 14

OUT = Path("data/sold.json")
DBG_DIR = Path("debug")
DBG_DIR.mkdir(parents=True, exist_ok=True)
OUT.parent.mkdir(parents=True, exist_ok=True)

SEARCH_URLS = [
    "https://jp.mercari.com/search?keyword={q}",
    "https://tw.mercari.com/zh-hant/search?keyword={q}",
]

SOLD_HINTS = ["SOLD", "売り切れ", "販売終了", "已售出", "售出"]

RE_NO = re.compile(r"(?:No\.?|NO\.?|№)\s*0*([0-9]{1,3})", re.IGNORECASE)

def now_taipei_iso():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")

def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def extract_no_and_name(title: str):
    t = normalize_space(title)
    m = RE_NO.search(t)
    if not m:
        return None, None
    no = int(m.group(1))
    after = t[m.end():].strip(" ：:・-—|()[]　")
    name = after.split(" ")[0] if after else None
    return no, name or None

def parse_price(text: str):
    t = normalize_space(text)
    m = re.search(r"[¥￥]\s*([0-9][0-9,]*)", t)
    if m:
        return "JPY", int(m.group(1).replace(",", ""))
    m = re.search(r"(?:NT\$|NT＄|NT)\s*([0-9][0-9,]*)", t, re.IGNORECASE)
    if m:
        return "TWD", int(m.group(1).replace(",", ""))
    return None, None

def is_sold_by_text(s: str) -> bool:
    return any(h in s for h in SOLD_HINTS)

def collect_item_links(page):
    """
    Mercari DOM 會變，所以用多組 selector：
    - a[href*="/item/"]
    - a[data-testid*="item"]（若有）
    - 直接用 JS 拿所有 href 再過濾
    """
    hrefs = set()

    # 1) 最穩：href 含 /item/
    try:
        loc = page.locator('a[href*="/item/"]')
        for i in range(min(loc.count(), 300)):
            h = loc.nth(i).get_attribute("href")
            if h:
                hrefs.add(h)
    except Exception:
        pass

    # 2) 可能存在的 testid
    try:
        loc = page.locator('a[data-testid*="item"]')
        for i in range(min(loc.count(), 300)):
            h = loc.nth(i).get_attribute("href")
            if h and "/item/" in h:
                hrefs.add(h)
    except Exception:
        pass

    # 3) 兜底：整頁 JS 拿 href
    try:
        all_hrefs = page.evaluate("""
          () => Array.from(document.querySelectorAll('a'))
            .map(a => a.getAttribute('href'))
            .filter(Boolean)
        """)
        for h in all_hrefs:
            if "/item/" in h:
                hrefs.add(h)
    except Exception:
        pass

    # 補全成完整 URL（以目前 page.url 的網域為主）
    base = page.url.split("/search")[0].rstrip("/")
    full = []
    for h in hrefs:
        if h.startswith("http"):
            full.append(h)
        else:
            full.append(base + h)

    # 去重 + 保序
    seen = set()
    out = []
    for u in full:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def dump_debug(page, tag: str):
    """存 HTML + screenshot 到 repo/debug/"""
    try:
        (DBG_DIR / f"{tag}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(DBG_DIR / f"{tag}.png"), full_page=True)
    except Exception:
        pass

def main():
    items = []
    meta = {
        "updated_at": now_taipei_iso(),   # 你會看到 +08:00（台灣）
        "count": 0,
        "items": items,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            locale="ja-JP",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        )
        page = context.new_page()

        for kw in KEYWORDS:
            q_enc = re.sub(r" ", "%20", kw)
            links = []

            for si, tpl in enumerate(SEARCH_URLS, start=1):
                url = tpl.format(q=q_enc)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(1500)

                    # 滾動載入
                    for _ in range(MAX_SCROLLS):
                        page.mouse.wheel(0, 2400)
                        page.wait_for_timeout(700)

                    links = collect_item_links(page)

                    # Debug：若抓不到任何 item link，存下來看
                    if not links:
                        dump_debug(page, f"search_{si}_no_links")
                        continue

                    break
                except PWTimeoutError:
                    dump_debug(page, f"search_{si}_timeout")
                    continue

            # 若還是 0，直接寫檔並退出（你會在 debug/ 看到頁面長怎樣）
            if not links:
                meta["count"] = 0
                OUT.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                print("No item links found. Check debug/*.png and debug/*.html")
                browser.close()
                return

            links = links[:MAX_ITEM_LINKS]
            print(f"[{kw}] Collected item links: {len(links)}")

            for idx, item_url in enumerate(links, start=1):
                try:
                    page.goto(item_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(900)

                    body_text = page.inner_text("body")
                    if not is_sold_by_text(body_text):
                        continue

                    # title
                    title = None
                    try:
                        title = page.locator("meta[property='og:title']").get_attribute("content")
                    except Exception:
                        pass
                    title = normalize_space(title or page.title())

                    # price
                    currency, amount = parse_price(body_text)

                    no, name_guess = extract_no_and_name(title)

                    items.append({
                        "keyword": kw,
                        "url": item_url,
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

    meta["count"] = len(items)
    OUT.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(items)} items -> {OUT}")

if __name__ == "__main__":
    main()
