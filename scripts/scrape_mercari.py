import json, re, time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path("data/sold.json")
DBG = Path("debug")
OUT.parent.mkdir(parents=True, exist_ok=True)
DBG.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
  "ポケパークカントー ピンバッジ",
  "pokemon park kanto pin",
]

HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Accept-Language": "ja,en-US;q=0.9,en;q=0.8,zh-TW;q=0.7,zh;q=0.6",
}

NO_RE = re.compile(r"(?:No\.?\s*)?0*(\d{1,3})", re.IGNORECASE)  # No. 0138 / 138
JPY_RE = re.compile(r"¥\s*([\d,]+)")

def now_iso():
  return datetime.now(timezone.utc).isoformat()

def fetch(url: str):
  r = requests.get(url, headers=HEADERS, timeout=30)
  return r.status_code, r.text

def parse_items_from_html(html: str):
  """
  這裡先用「可存活」策略：
  - 先把所有 a[href*="/item/"] 的連結抓出來（Mercari 常見）
  - 同時嘗試在頁面上找 price 字樣（未必有）
  - 解析不到也沒關係，debug 會留
  """
  soup = BeautifulSoup(html, "html.parser")
  links = []
  for a in soup.select('a[href]'):
    href = a.get("href", "")
    if "/item/" in href:
      links.append(href.split("?")[0])
  # 去重
  seen = set()
  uniq = []
  for h in links:
    if h not in seen:
      seen.add(h)
      uniq.append(h)

  return uniq

def extract_no_and_name_from_title(title: str):
  # 例：ポケパーク カントー ピンバッジ No. 0138 オムナイト
  m = re.search(r"No\.?\s*0*(\d{1,3})", title, re.IGNORECASE)
  no = int(m.group(1)) if m else None

  # 粗略取最後一段當作寶可夢名（你後面可再用對照表修正）
  name = title.strip()
  name = re.sub(r".*No\.?\s*0*\d{1,3}\s*", "", name, flags=re.IGNORECASE).strip()
  return no, name

def main():
  all_items = []
  debug_notes = []

  for kw in KEYWORDS:
    q = requests.utils.quote(kw)
    search_url = f"https://jp.mercari.com/search?keyword={q}"
    status, html = fetch(search_url)

    # 存 debug
    safe = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]+", "_", kw)[:60]
    dbg_file = DBG / f"search_{safe}.html"
    dbg_file.write_text(html, encoding="utf-8")

    debug_notes.append({"keyword": kw, "url": search_url, "status": status, "debug_html": str(dbg_file)})

    # 嘗試從搜尋頁抓 item 連結（若抓不到，至少 debug 有留）
    item_paths = parse_items_from_html(html)

    # 只取前 60 個避免太重
    item_paths = item_paths[:60]

    # 逐一抓 item 頁，抽標題/價格/是否 sold（sold 不一定能判斷，先收集）
    for p in item_paths:
      item_url = p if p.startswith("http") else ("https://jp.mercari.com" + p)
      st2, html2 = fetch(item_url)
      # 存 item debug（只存少量）
      item_id = item_url.split("/")[-1]
      (DBG / f"item_{item_id}.html").write_text(html2, encoding="utf-8")

      s2 = BeautifulSoup(html2, "html.parser")
      title = (s2.find("title").get_text(strip=True) if s2.find("title") else "").strip()

      # 價格抓不到就 None（很多時候 sold 價也不會顯示在靜態 HTML）
      txt = s2.get_text(" ", strip=True)
      pm = JPY_RE.search(txt)
      price = int(pm.group(1).replace(",", "")) if pm else None

      no, poke_name = extract_no_and_name_from_title(title or "")
      if no is None:
        continue

      all_items.append({
        "no": no,
        "pokemon_name_raw": poke_name,
        "title": title,
        "price_jpy": price,
        "url": item_url,
        "fetched_at": now_iso(),
        "status_code": st2,
      })

      time.sleep(0.8)

    time.sleep(1.2)

  # 輸出
  payload = {
    "updated_at": now_iso(),
    "count": len(all_items),
    "items": sorted(all_items, key=lambda x: (x["no"], x.get("price_jpy") or 10**12)),
    "debug": debug_notes
  }
  OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
  print(json.dumps({"count": payload["count"], "debug_files": [d["debug_html"] for d in debug_notes]}, ensure_ascii=False))

if __name__ == "__main__":
  main()
