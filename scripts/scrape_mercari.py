import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("data/sold.json")
DBG_DIR = Path("debug")
OUT.parent.mkdir(parents=True, exist_ok=True)
DBG_DIR.mkdir(parents=True, exist_ok=True)

# 只用日文搜尋（你要求取消英文選項）
KEYWORD = "ポケパークカントー ピンバッジ"
STATUSES = ["sold_out", "on_sale"]  # 已售出 / 交易中

# 目標：Kanto 151（用「名稱命中」為主；No. 有就用）
# 這份是標準日文名稱（カントー図鑑）
JP = ["",
"フシギダネ","フシギソウ","フシギバナ","ヒトカゲ","リザード","リザードン","ゼニガメ","カメール","カメックス",
"キャタピー","トランセル","バタフリー","ビードル","コクーン","スピアー","ポッポ","ピジョン","ピジョット",
"コラッタ","ラッタ","オニスズメ","オニドリル","アーボ","アーボック","ピカチュウ","ライチュウ","サンド","サンドパン",
"ニドラン♀","ニドリーナ","ニドクイン","ニドラン♂","ニドリーノ","ニドキング","ピッピ","ピクシー","ロコン","キュウコン",
"プリン","プクリン","ズバット","ゴルバット","ナゾノクサ","クサイハナ","ラフレシア","パラス","パラセクト","コンパン",
"モルフォン","ディグダ","ダグトリオ","ニャース","ペルシアン","コダック","ゴルダック","マンキー","オコリザル","ガーディ",
"ウインディ","ニョロモ","ニョロゾ","ニョロボン","ケーシィ","ユンゲラー","フーディン","ワンリキー","ゴーリキー","カイリキー",
"マダツボミ","ウツドン","ウツボット","メノクラゲ","ドククラゲ","イシツブテ","ゴローン","ゴローニャ","ポニータ","ギャロップ",
"ヤドン","ヤドラン","コイル","レアコイル","カモネギ","ドードー","ドードリオ","パウワウ","ジュゴン","ベトベター",
"ベトベトン","シェルダー","パルシェン","ゴース","ゴースト","ゲンガー","イワーク","スリープ","スリーパー","クラブ",
"キングラー","ビリリダマ","マルマイン","タマタマ","ナッシー","カラカラ","ガラガラ","サワムラー","エビワラー","ベロリンガ",
"ドガース","マタドガス","サイホーン","サイドン","ラッキー","モンジャラ","ガルーラ","タッツー","シードラ","トサキント",
"アズマオウ","ヒトデマン","スターミー","バリヤード","ストライク","ルージュラ","エレブー","ブーバー","カイロス","ケンタロス",
"コイキング","ギャラドス","ラプラス","メタモン","イーブイ","シャワーズ","サンダース","ブースター","ポリゴン","オムナイト",
"オムスター","カブト","カブトプス","プテラ","カビゴン","フリーザー","サンダー","ファイヤー","ミニリュウ","ハクリュー",
"カイリュー","ミュウツー","ミュウ"
]
JP_TO_NO = {name: i for i, name in enumerate(JP) if name}

# 雜訊字尾清理：你現在抓到「〇〇のサムネイル」這種要修掉
NOISE_SUFFIX_RE = re.compile(r"(の)?サムネイル.*$", re.IGNORECASE)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def safe_filename(s: str) -> str:
    s = s.strip().replace(" ", "_")
    s = re.sub(r"[^0-9A-Za-z_\-\u3040-\u30FF\u4E00-\u9FFF]+", "_", s)
    return s[:120] if len(s) > 120 else s

def build_search_url(keyword: str, status: str) -> str:
    # status=sold_out / on_sale
    # ※ Mercari 參數可能調整，但你目前 debug 已證實這組能回頁面
    from urllib.parse import quote
    return f"https://jp.mercari.com/search?keyword={quote(keyword)}&status={status}"

def extract_no_from_title(title: str):
    # 接受 No. 0138 / No.0138 / NO.0052 / No 52
    m = re.search(r"\b(?:No\.?|NO\.?)\s*0*([0-9]{1,3})\b", title)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 151 else None

def extract_pokemon_from_title(title: str):
    """
    規則（照你的需求）：
    - 不要求一定有 No.
    - 只要標題含寶可夢名稱就算命中
    - 同時把「のサムネイル」這種尾巴去掉
    """
    t = NOISE_SUFFIX_RE.sub("", title).strip()

    # 先用 No. 來定位（有 No. 就更準）
    no = extract_no_from_title(t)
    if no:
        # 嘗試從 No 後面取名字（例如：No.0138 オムナイト）
        m = re.search(r"(?:No\.?|NO\.?)\s*0*[0-9]{1,3}\s*([^\s　]+)", t)
        if m:
            cand = m.group(1).strip("　 ").strip()
            cand = NOISE_SUFFIX_RE.sub("", cand).strip()
            # 若剛好是標準日文名就直接映射
            if cand in JP_TO_NO:
                return no, cand
        # 沒抓到名稱也沒關係，至少有 no
        return no, JP[no] if no < len(JP) else ""

    # 沒有 No：用名稱掃描（151 名稱中有出現就命中）
    for name in JP_TO_NO.keys():
        if name and name in t:
            return JP_TO_NO[name], name

    return None, ""

def scrape():
    items = []
    debug = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ja-JP",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        )

        for status in STATUSES:
            url = build_search_url(KEYWORD, status)
            dbg_html_name = f"search_{safe_filename(KEYWORD)}_{status}.html"
            dbg_path = DBG_DIR / dbg_html_name

            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # 盡量讓列表真的載入（避免你看到那種「只有標題沒結果」的空殼）
            try:
                page.wait_for_timeout(1500)
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(1500)
            except Exception:
                pass

            # 存 debug html（你要自行確認就看這檔）
            dbg_path.write_text(page.content(), encoding="utf-8", errors="ignore")

            # 收集 item 連結（去重）
            hrefs = page.locator('a[href^="/item/"]').evaluate_all(
                "els => Array.from(new Set(els.map(e => e.getAttribute('href')).filter(Boolean)))"
            )

            # 轉成完整 URL
            item_urls = [("https://jp.mercari.com" + h) for h in hrefs if re.match(r"^/item/m\d+", h)]

            d = {
                "keyword": KEYWORD,
                "status": status,
                "url": url,
                "collected_links": len(item_urls),
                "visited_items": 0,
                "matched_items": 0,
                "price_null": 0,
                "errors": 0,
                "debug_html": f"debug/{dbg_html_name}",
            }

            # 逐筆打開 item 頁抓 title / price
            # 控制數量避免太重（你要更多就把這個調大）
            MAX_VISIT = 60 if status == "sold_out" else 40
            item_urls = item_urls[:MAX_VISIT]

            for item_url in item_urls:
                d["visited_items"] += 1
                ip = ctx.new_page()
                try:
                    ip.goto(item_url, wait_until="domcontentloaded", timeout=60000)
                    ip.wait_for_timeout(500)

                    # title：優先 og:title，再退回 h1
                    og = ip.locator('meta[property="og:title"]').get_attribute("content")
                    title = (og or "").strip()
                    if not title:
                        h1 = ip.locator("h1").first
                        if h1.count() > 0:
                            title = h1.inner_text().strip()

                    if not title:
                        continue

                    no, poke = extract_pokemon_from_title(title)
                    if not no:
                        # 你要「只要寶可夢名稱」：沒命中就跳過
                        continue

                    # price：盡量用 meta 取（通常最穩），不行再用頁面文字抓 ¥
                    price = None
                    meta_price = ip.locator('meta[property="product:price:amount"]').get_attribute("content")
                    if meta_price and meta_price.isdigit():
                        price = int(meta_price)
                    else:
                        txt = ip.content()
                        m = re.search(r"¥\s*([0-9,]{2,})", txt)
                        if m:
                            price = int(m.group(1).replace(",", ""))

                    if price is None:
                        d["price_null"] += 1

                    items.append({
                        "keyword": KEYWORD,
                        "listing_status": status,     # sold_out / on_sale
                        "no": int(no),
                        "pokemon_name": poke,
                        "title": title,
                        "price_jpy": price,
                        "item_url": item_url,
                        "observed_at": now_iso(),     # 用抓取時間當觀測點（圖表可用）
                    })
                    d["matched_items"] += 1

                except Exception:
                    d["errors"] += 1
                finally:
                    ip.close()

            debug.append(d)
            page.close()

        browser.close()

    payload = {
        "updated_at": now_iso(),
        "count": len(items),
        "items": items,
        "debug": debug,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    scrape()
