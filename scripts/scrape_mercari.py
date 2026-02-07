import json, re, time, requests
from pathlib import Path
from datetime import datetime, timezone
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DEBUG = BASE / "debug"
DATA.mkdir(exist_ok=True)
DEBUG.mkdir(exist_ok=True)

OUT = DATA / "sold.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ja-JP,ja;q=0.9"
}

KEYWORDS = [
    "ポケパークカントー ピンバッジ"
]

STATUSES = {
    "sold_out": "已售出",
    "on_sale": "交易中"
}

# === 寶可夢 1~151 日文名稱（乾淨版）===
POKEMON = [
    "", "フシギダネ","フシギソウ","フシギバナ","ヒトカゲ","リザード","リザードン","ゼニガメ","カメール","カメックス",
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

def detect_pokemon(title: str):
    for no in range(1, 152):
        name = POKEMON[no]
        if name and name in title:
            return no, name
    return None, None

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text

items = []
debug = []

for kw in KEYWORDS:
    for status in STATUSES:
        url = f"https://jp.mercari.com/search?keyword={kw}&status={status}"
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        links = []
        for a in soup.select("a[href^='/item/']"):
            links.append("https://jp.mercari.com" + a["href"].split("?")[0])

        links = list(dict.fromkeys(links))

        matched = 0
        for link in links:
            time.sleep(0.3)
            try:
                ih = fetch(link)
                isoup = BeautifulSoup(ih, "html.parser")
                title = isoup.find("title").get_text(strip=True)

                no, name = detect_pokemon(title)
                if not name:
                    continue

                matched += 1
                items.append({
                    "keyword": kw,
                    "listing_status": status,
                    "no": no,
                    "pokemon_name": name,
                    "title": title,
                    "price_jpy": None,
                    "item_url": link,
                    "scraped_at": datetime.now(timezone.utc).isoformat()
                })
            except:
                continue

        debug.append({
            "keyword": kw,
            "status": status,
            "url": url,
            "collected_links": len(links),
            "matched_items": matched
        })

out = {
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "count": len(items),
    "items": items,
    "debug": debug
}

OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
