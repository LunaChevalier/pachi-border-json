import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://p-town.dmm.com"
CALENDAR_URL = f"{BASE_URL}/machines/new_calendar"
MACHINE_URL = f"{BASE_URL}/machines/{{id}}"
OUTPUT_FILE = "suggestions.json"

# ●貸玉料金XX円 セクションのレートキーへのマッピング
RATE_MAP = {
    "4.3円": "YEN_4_3",
    "4円": "YEN_4",
    "1円": "YEN_1",
}


def fetch_calendar_html(page, url):
    """カレンダーページを開き、機種リストが表示されるまで待機してHTMLを返す"""
    print(f"Fetching: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("ul.list-machineintroduction", timeout=15000)
    except Exception:
        pass
    print(f"Fetched: {url}")
    return page.content()


def fetch_machine_html(page, url):
    """機種ページを開き、ボーダーセクションが表示されるまで待機してHTMLを返す"""
    print(f"Fetching: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("h5[id^='anc-title-border-']", timeout=5000)
    except Exception:
        pass
    print(f"Fetched: {url}")
    return page.content()


def fetch_calendar(page):
    """新台カレンダーから最新「導入」日のパチンコ機種を取得する"""
    today = date.today()
    url = f"{CALENDAR_URL}?year={today.year}&month={today.month}"
    print(f"Fetching: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.ok:
        html = resp.text
    else:
        # requests がブロックされた場合は Playwright にフォールバック
        print(f"requests failed ({resp.status_code}), falling back to Playwright")
        html = fetch_calendar_html(page, url)
    print(f"Fetched: {url}")
    soup = BeautifulSoup(html, "html.parser")

    # ul.list-machineintroduction > li.item から機種と日付を収集
    machines = []
    for li in soup.select("ul.list-machineintroduction li.item"):
        # パチンコのみ（-pinball クラスのspanを持つ）
        if not li.find("span", class_="-pinball"):
            continue
        a = li.find("a", class_="link")
        if not a:
            continue
        href = a.get("href", "")
        id_match = re.match(r"/machines/(\d+)", href)
        if not id_match:
            continue
        name = a.get_text(strip=True)
        date_div = li.find("div", class_="date")
        if not date_div:
            continue
        date_match = re.search(r"(\d{4})年(\d{2})月(\d{2})日", date_div.get_text())
        if not date_match:
            continue
        release_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
        machines.append({
            "id": id_match.group(1),
            "name": name,
            "releaseDate": release_date,
        })

    if not machines:
        return []

    # 今日以前の最新日付を選ぶ（未来のみなら最も近い未来日付）
    today_str = today.strftime("%Y-%m-%d")
    past_dates = [m["releaseDate"] for m in machines if m["releaseDate"] <= today_str]
    target_date = max(past_dates) if past_dates else min(m["releaseDate"] for m in machines)

    return [m for m in machines if m["releaseDate"] == target_date]


def parse_borders(wysiwyg_text):
    """wysiwyg-boxのテキストからボーダー情報を解析する"""
    borders = []
    # ●貸玉料金 で各セクションに分割
    sections = re.split(r"●貸玉料金", wysiwyg_text)
    for section in sections:
        lines = [l.strip() for l in section.strip().splitlines() if l.strip()]
        if not lines:
            continue
        # 先頭行がセクションヘッダ: "4.3円（232個あたり）" 等
        header_match = re.match(r"([\d.]+円)", lines[0])
        if not header_match:
            continue
        rate_key = header_match.group(1)
        rate = RATE_MAP.get(rate_key)
        if not rate:
            continue
        # セクション内の最初のボーダー値を取得: "4.0円（25個）…16.1回転"
        for line in lines[1:]:
            value_match = re.search(r"…([\d.]+)回転", line)
            if value_match:
                borders.append({
                    "rate": rate,
                    "baselineValue": float(value_match.group(1)),
                })
                break

    return borders


def fetch_machine_data(page, machine_id):
    """機種IDからボーダー情報とひらがな名を取得する"""
    url = MACHINE_URL.format(id=machine_id)
    try:
        html = fetch_machine_html(page, url)
    except Exception:
        return [], ""

    soup = BeautifulSoup(html, "html.parser")

    # ひらがな名を取得: div.titleruby > p.ruby
    kana = ""
    ruby_p = soup.select_one("div.titleruby p.ruby")
    if ruby_p:
        kana = ruby_p.get_text(strip=True)

    all_borders = []

    # h5[id^="anc-title-border-"] の直後の div.wysiwyg-box を解析
    for h5 in soup.find_all("h5", id=re.compile(r"^anc-title-border-")):
        wysiwyg = h5.find_next_sibling("div")
        if wysiwyg:
            all_borders.extend(parse_borders(wysiwyg.get_text()))

    # 同一レートが複数ある場合（設定差など）はボーダーが大きい方を採用
    best: dict[str, dict] = {}
    for entry in all_borders:
        rate = entry["rate"]
        if rate not in best or entry["baselineValue"] > best[rate]["baselineValue"]:
            best[rate] = entry

    return list(best.values()), kana


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print("新台カレンダーを取得中...")
        machines = fetch_calendar(page)
        print(f"{len(machines)}件の機種を取得しました")

        if not machines:
            print("機種が見つかりませんでした。処理を終了します")
            browser.close()
            return

        fetched = []
        for i, machine in enumerate(machines, 1):
            print(f"[{i}/{len(machines)}] {machine['name']} のボーダー情報を取得中...")
            borders, kana = fetch_machine_data(page, machine["id"])
            fetched.append({
                "id": machine["id"],
                "name": machine["name"],
                "kana": kana,
                "url": MACHINE_URL.format(id=machine["id"]),
                "releaseDate": machine["releaseDate"],
                "borders": borders,
            })

        browser.close()

    # いずれの機種もボーダー情報が取得できなかった場合はJSONを更新しない
    if not any(r["borders"] for r in fetched):
        print("ボーダー情報が取得できませんでした。JSONを更新せずに終了します")
        return

    # 既存JSONを読み込み、idをキーにupsert
    output_path = Path(OUTPUT_FILE)
    existing: list[dict] = []
    if output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)

    existing_map = {entry["id"]: entry for entry in existing if "id" in entry}
    for entry in fetched:
        existing_map[entry["id"]] = entry

    results = list(existing_map.values())

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"{OUTPUT_FILE} を更新しました（全{len(results)}件 / 今回{len(fetched)}件upsert）")


if __name__ == "__main__":
    main()
