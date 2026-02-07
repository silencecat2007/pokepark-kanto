import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("data/sold.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

# 你要的關鍵字（不含編號）
KEYWORDS = [
    "pokemon park kanto pin",
    "ポケパークカントー ピンバッジ",
]

# Mercari JP status 參數常見值：sold_out / on_sale
STATUSES = ["sold_out", "on_sale"]

# 只收 1~151，且標題要能抓到 No. xxx
NO_RE = re.compile(r"\bNo\.?\s*0*(\d{1,3})\b", re.IGNORECASE)

# 取 No.後面可能接寶可夢名（例：No. 0138 オムナイト）
# 會抓一段連續字元當作名稱（遇到空白/符號停止）
NAME_AFTER_NO_RE = re.compile(r"\bNo\.?\s*0*\d{1,3}\s*([^\s／/|【】\[\]（）()]+)", re.IGNORECASE)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def build_search_url(keyword: str, status: str) -> str:
    # 你要的：不帶編號，直接用關鍵字搜；再用 status 抓「已售出 / 交易中」
    # 備註：Mercari 參數有時會變，但這個寫法是最常見的
    return f"https://jp.mercari.com/search?keyword={quote(keyword)}&status={status}"

def quote(s: str) -> str:
    # 避免額外依賴 urllib
    from urllib.parse import quote as _q
    return _q(s, safe="")

def text_to_jpy(text: str):
    # "¥12,345" -> 12345
    if not text:
        return None
    t = text.replace(",", "").replace("￥", "¥")
    m = re.search(r"¥\s*(\d+)", t)
    return int(m.group(1)) if m else None

def extract_no_and_name(title: str):
    if not title:
        return None, None
    m = NO_RE.search(title)
    if not m:
        return None, None
    no = int(m.group(1))
    if not (1 <= no <= 151):
        return None, None

    nm = NAME_AFTER_NO_RE.search(title)
    name = nm.group(1).strip() if nm else None
    return no, name

def sleep_small():
    time.sleep(random.uniform(0.6, 1.2))

def main():
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

                # 等商品網格出現（Mercari 可能會變 class，所以用比較寬鬆的策略）
                # 下面策略：等到頁面內至少出現一個 item 連結（/item/ 開頭）
                try:
                    page.wait_for_selector("a[href^='/item/']", timeout=20000)
                except Exception:
                    # 抓不到就記錄 debug，繼續下一個
                    debug.append({"keyword": kw, "status": st, "url": url, "note": "no item links found"})
                    continue

                # 你可以視需要調大，避免太重：先抓前 120 筆（含滾動載入）
                max_collect = 120
                collected = set()

                # 滾動幾次把更多結果載出來
                for _ in range(8):
                    links = page.query_selector_all("a[href^='/item/']")
                    for a in links:
                        href = a.get_attribute("href") or ""
                        if not href.startswith("/item/"):
                            continue
                        full = "https://jp.mercari.com" + href
                        collected.add(full)
                        if len(collected) >= max_collect:
                            break
                    if len(collected) >= max_collect:
                        break
                    page.mouse.wheel(0, 1600)
                    sleep_small()

                # 針對每個 item card 抓 title/price
                # 這裡不進 item 詳情頁，減少被擋與耗時
                for item_url in list(collected):
                    # 在搜尋頁上找對應的 a，再往上抓卡片區塊
                    a = page.query_selector(f"a[href='{item_url.replace('https://jp.mercari.com','')}']")
                    if not a:
                        continue

                    # card 容器：往上找最近的 li/div
                    # Playwright 的 ElementHandle 沒有 locator，所以用 evaluate 找最近父層
                    container = a.evaluate_handle(
                        """(el) => el.closest('li') || el.closest('div') || el.parentElement"""
                    )
                    if not container:
                        continue

                    title = None
                    price = None

                    # title 常見在 img alt 或文字區塊
                    img = container.query_selector("img[alt]")
                    if img:
                        title = img.get_attribute("alt")

                    if not title:
                        # 備援：抓 container 文字
                        txt = container.inner_text().strip()
                        # Mercari 的卡片文字很多，先取前 120 字當 title 候選
                        title = txt.split("\n")[0][:120] if txt else None

                    # price 常見出現在含 ¥ 的文字
                    t = container.inner_text()
                    price = text_to_jpy(t)

                    no, pkm_name = extract_no_and_name(title or "")
                    if not no:
                        continue  # 只收有 No. 001~151 的

                    items.append(
                        {
                            "keyword": kw,
                            "listing_status": st,  # sold_out / on_sale
                            "no": no,
                            "pokemon_name": pkm_name,
                            "title": title,
                            "price_jpy": price,
                            "item_url": item_url,
                            "scraped_at": now_iso(),
                        }
                    )

                debug.append({"keyword": kw, "status": st, "url": url, "collected_links": len(collected)})

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
