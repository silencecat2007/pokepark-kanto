import re
import json
import time
import random
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("data/sold.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
    "pokemon park kanto pin",
    "ポケパークカントー ピンバッジ",
]

HEADLESS = True
SLEEP_BETWEEN_PAGES = (1.5, 2.5)


def money_to_int(text: str | None):
    if not text:
        return None
    m = re.search(r"¥\s*([\d,]+)", text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def parse_no_and_name(title: str):
    """
    嘗試從標題抓：
    - No. 001
    - 001
    - No001
    """
    if not title:
        return None, None

    m = re.search(r"(?:No\.?\s*)?(\d{1,3})", title)
    no = int(m.group(1)) if m else None
    return no, title.strip()


def scrape_keyword(page, keyword: str):
    url = f"https://jp.mercari.com/search?keyword={keyword.replace(' ', '%20')}"
    page.goto(url, timeout=60000)
    page.wait_for_timeout(2000)

    items = []

    # ⚠️ 關鍵：用 locator，不用 query_selector_all
    links = page.locator('a[href^="/item/"]')
    count = links.count()

    for i in range(count):
        a = links.nth(i)

        href = a.get_attribute("href") or ""
        if not href.startswith("/item/"):
            continue

        item_url = "https://jp.mercari.com" + href

        # 找最近的卡片容器
        container = a.locator(
            "xpath=ancestor-or-self::*[self::li or self::div][1]"
        )

        # 標題
        title = a.get_attribute("aria-label")
        if not title:
            try:
                title = a.inner_text().strip()
            except:
                title = ""

        # 價格
        price = None
        try:
            price_text = container.locator(
                'xpath=.//*[contains(text(),"¥")]'
            ).first.inner_text().strip()
            price = money_to_int(price_text)
        except:
            pass

        if price is None:
            continue

        no, name = parse_no_and_name(title)

        items.append(
            {
                "keyword": keyword,
                "title": title,
                "price_jpy": price,
                "item_url": item_url,
                "sold_at": datetime.now(timezone.utc).isoformat(),
                "no": no,
                "pokemon_name": name,
            }
        )

    return items, url


def main():
    all_items = []
    debug = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()

        for kw in KEYWORDS:
            items, search_url = scrape_keyword(page, kw)
            all_items.extend(items)
            debug.append(
                {
                    "keyword": kw,
                    "url": search_url,
                    "status": 200,
                }
            )
            time.sleep(random.uniform(*SLEEP_BETWEEN_PAGES))

        browser.close()

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(all_items),
        "items": all_items,
        "debug": debug,
    }

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
