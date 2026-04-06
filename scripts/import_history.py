"""
過去の月別カレンダーからパチンコ機種とボーダーを一括取込するスクリプト。
使い方: python scripts/import_history.py [開始年] [終了年]
例:    python scripts/import_history.py 2024 2024
"""

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://p-town.dmm.com"
CALENDAR_MONTH_URL = f"{BASE_URL}/machines/new_calendar?year={{year}}&month={{month}}"
MACHINE_URL = f"{BASE_URL}/machines/{{id}}"
OUTPUT_FILE = Path("suggestions.json")

RATE_MAP = {
    "4.3円": "YEN_4_3",
    "4円": "YEN_4",
    "1円": "YEN_1",
}


def fetch_page(page, url, wait_selector=None, timeout=5000):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=timeout)
        except Exception:
            pass
    return page.content()


def fetch_machines_for_month(page, year, month):
    """月別カレンダーページからパチンコ機種一覧を取得する"""
    url = CALENDAR_MONTH_URL.format(year=year, month=month)
    html = fetch_page(page, url, wait_selector="li.item")
    soup = BeautifulSoup(html, "html.parser")

    machines = []
    block = soup.find("div", class_=["default-box", "-machine"])
    if not block:
        return machines

    for item in block.find_all("li", class_="item"):
        # パチンコのみ（-pinball クラスのspanを持つ）
        if not item.find("span", class_="-pinball"):
            continue

        link = item.find("a", class_="link")
        if not link:
            continue
        href = link.get("href", "")
        id_match = re.match(r"/machines/(\d+)", href)
        if not id_match:
            continue

        date_div = item.find("div", class_="date")
        if not date_div:
            continue
        date_match = re.search(r"(\d{4})年(\d{2})月(\d{2})日", date_div.get_text())
        if not date_match:
            continue

        machines.append({
            "id": id_match.group(1),
            "name": link.get_text(strip=True),
            "releaseDate": f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}",
        })

    return machines


def parse_borders(wysiwyg_text):
    borders = []
    sections = re.split(r"●貸玉料金", wysiwyg_text)
    for section in sections:
        lines = [l.strip() for l in section.strip().splitlines() if l.strip()]
        if not lines:
            continue
        header_match = re.match(r"([\d.]+円)", lines[0])
        if not header_match:
            continue
        rate = RATE_MAP.get(header_match.group(1))
        if not rate:
            continue
        for line in lines[1:]:
            value_match = re.search(r"…([\d.]+)回転", line)
            if value_match:
                borders.append({"rate": rate, "baselineValue": float(value_match.group(1))})
                break
    return borders


def fetch_machine_data(page, machine_id):
    """機種IDからボーダー情報とひらがな名を取得する"""
    url = MACHINE_URL.format(id=machine_id)
    try:
        html = fetch_page(page, url, wait_selector="h5[id^='anc-title-border-']", timeout=5000)
    except Exception:
        return [], ""

    soup = BeautifulSoup(html, "html.parser")

    # ひらがな名を取得: div.titleruby > p.ruby
    kana = ""
    ruby_p = soup.select_one("div.titleruby p.ruby")
    if ruby_p:
        kana = ruby_p.get_text(strip=True)

    all_borders = []
    for h5 in soup.find_all("h5", id=re.compile(r"^anc-title-border-")):
        wysiwyg = h5.find_next_sibling("div")
        if wysiwyg:
            all_borders.extend(parse_borders(wysiwyg.get_text()))

    best: dict[str, dict] = {}
    for entry in all_borders:
        rate = entry["rate"]
        if rate not in best or entry["baselineValue"] > best[rate]["baselineValue"]:
            best[rate] = entry
    return list(best.values()), kana


def load_existing():
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save(data):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    start_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    end_year = int(sys.argv[2]) if len(sys.argv) > 2 else start_year

    # 既存データをidをキーにしたマップとして読み込む
    existing_map = {e["id"]: e for e in load_existing() if "id" in e}
    print(f"既存データ: {len(existing_map)}件")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                print(f"\n--- {year}年{month}月 ---")
                machines = fetch_machines_for_month(page, year, month)
                pinball = [m for m in machines]
                print(f"パチンコ: {len(pinball)}件")

                for i, machine in enumerate(pinball, 1):
                    mid = machine["id"]
                    # 既にボーダーデータとひらがな名がある場合はスキップ
                    if mid in existing_map and existing_map[mid].get("borders") and existing_map[mid].get("kana"):
                        print(f"  [{i}/{len(pinball)}] スキップ: {machine['name']}")
                        continue

                    print(f"  [{i}/{len(pinball)}] {machine['name']} のデータを取得中...")
                    borders, kana = fetch_machine_data(page, mid)
                    existing_map[mid] = {
                        "id": mid,
                        "name": machine["name"],
                        "kana": kana,
                        "url": MACHINE_URL.format(id=mid),
                        "releaseDate": machine["releaseDate"],
                        "borders": borders,
                    }

                # 月ごとに中間保存
                save(list(existing_map.values()))
                print(f"  保存済み（累計: {len(existing_map)}件）")

        browser.close()

    print(f"\n完了: suggestions.json に {len(existing_map)}件保存しました")


if __name__ == "__main__":
    main()
