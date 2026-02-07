import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("data/sold.json")
DEBUG_DIR = Path("debug")
OUT.parent.mkdir(parents=True, exist_ok=True)
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

# 你要求：搜尋條件去掉編號，只用關鍵字
KEYWORDS = [
    "ポケパークカントー ピンバッジ",
    "pokemon park kanto pin",
]

# Mercari：status=sold_out|trading 會包含已售出/交易中（至少不會只剩「販售中」）
def build_search_url(keyword: str) -> str:
    from urllib.parse import quote
    return (
        "https://jp.mercari.com/search"
        f"?keyword={quote(keyword)}"
        "&status=sold_out%7Ctrading"
        "&order=desc&sort=created_time"
    )

NO_RE = re.compile(r"No\.?\s*0*([0-9]{1,3})", re.IGNORECASE)

def extract_no_and_name(title: str):
    """
    例：
    'ポケパーク カントー ピンバッジ No. 0138 オムナイト'
    -> no=138, pokemon_name='オムナイト'
    """
    m = NO_RE.search(title or "")
    if not m:
        return None, None
    no = int(m.group(1))

    # 名稱：取 No. #### 之後的文字，去掉多餘符號後取最後一段
    tail = (title[m.end():] if title else "").strip()
    tail = re.sub(r"[｜|/（）()\[\]【】]+", " ", tail).strip()
    pokemon_name = tail.split()[-1] if tail else None
    return no, pokemon_name

def money_to_int(text: str):
    # "¥12,999" -> 12999
    if not text:
        return None
    t = text.replace("¥", "").replace(",", "").strip()
    return int(t) if t.isdigit() else None

def main():
    updated_at = datetime.now(timezone.utc).isoformat()
    all_items = []
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
            url = build_search_url(kw)
            page.goto(url, wait_until="domcontentloaded")

            # 等商品卡片出現（Mercari 可能會延遲載入）
            # 找不到就存 debug 讓你看
            try:
                page.wait_for_timeout(1200)
                # 滾動幾次讓更多結果載入
                for _ in range(5):
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(800)

                html = page.content()
            except Exception:
                html = page.content()

            dbg_name = f"search_{kw}.html"
            dbg_name = (
                dbg_name.replace(" ", "_")
                .replace("/", "_")
                .replace("\\", "_")
            )
            dbg_path = DEBUG_DIR / dbg_name
            dbg_path.write_text(html, encoding="utf-8")

            debug.append({
                "keyword": kw,
                "url": url,
                "debug_html": str(dbg_path).replace("\\", "/"),
            })

            # ✅ 從 DOM 抓商品卡片（Mercari 版面會變，這組 selector 盡量寫寬鬆）
            # 抓所有 /item/xxxxx 的連結，再往上找價格/標題
            links = page.query_selector_all('a[href^="/item/"]')
            seen = set()

            for a in links:
                href = a.get_attribute("href") or ""
                if not href.startswith("/item/"):
                    continue
                item_url = "https://jp.mercari.com" + href
                if item_url in seen:
                    continue
                seen.add(item_url)

                # 盡量從同一張卡片容器內找 title/price
                container = a.locator("xpath=ancestor-or-self::*[self::li or self::div][1]")
                title = None
                price = None

                # title：常見是 aria-label 或卡片內文字
                title = a.get_attribute("aria-label")
                if not title:
                    try:
                        title = a.inner_text().strip()
                    except Exception:
                        title = None

                # price：抓包含 ¥ 的文字（卡片內通常有）
                try:
                    price_text = container.locator('xpath=.//*[contains(text(),"¥")]').first.inner_text().strip()
                    price = money_to_int(price_text)
                except Exception:
                    price = None

                no, pokemon_name = extract_no_and_name(title or "")
                if not no:
                    continue

                all_items.append({
                    "keyword": kw,
                    "no": no,
                    "pokemon_name": pokemon_name,
                    "title": title,
                    "price_jpy": price,
                    # 沒有「真實成交時間」就用抓取時間當時間軸（每天跑一次就能看趨勢）
                    "sold_at": updated_at,
                    "item_url": item_url,
                })

        context.close()
        browser.close()

    payload = {
        "updated_at": updated_at,
        "count": len(all_items),
        "items": all_items,
        "debug": debug,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
