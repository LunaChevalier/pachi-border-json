import json
import re
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://p-town.dmm.com"
CALENDAR_URL = f"{BASE_URL}/machines/new_calendar"
MACHINE_URL = f"{BASE_URL}/machines/{{id}}"
OUTPUT_FILE = "suggestions.json"
HTML_DIR = Path("html")

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
        page.wait_for_selector("li.unit", timeout=15000)
    except Exception:
        pass
    print(f"Fetched: {url}")
    return page.content()


def fetch_machine_html(page, url, machine_id):
    """機種ページを開き、ボーダーセクションが表示されるまで待機してHTMLを返す"""
    print(f"Fetching: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("h5[id^='anc-title-border-']", timeout=5000)
    except Exception:
        pass
    html = page.content()

    HTML_DIR.mkdir(exist_ok=True)
    (HTML_DIR / f"{machine_id}.html").write_text(html, encoding="utf-8")

    print(f"Fetched: {url}")
    return html


def fetch_calendar(page):
    """新台カレンダーから最新「導入」日のパチンコ機種を取得する"""
    html = fetch_calendar_html(page, CALENDAR_URL)
    soup = BeautifulSoup(html, "html.parser")

    # section.spacebody ごとに日付と機種リストを収集
    # p.title の形式: "2026年04月06日(月)導入" or "2026年04月20日(月)予定"
    date_sections = []
    for section in soup.find_all("section", class_="spacebody"):
        h3 = section.find("h3", class_="-machine")
        if not h3:
            continue
        title = h3.find("p", class_="title")
        if not title:
            continue
        title_text = title.get_text(strip=True)
        date_match = re.search(r"(\d{4})年(\d{2})月(\d{2})日", title_text)
        if not date_match:
            continue
        release_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
        date_sections.append((release_date, section))

    if not date_sections:
        return []

    # 今日以前の日付のみ対象にし、その中で最新の日付を選ぶ
    today = date.today().isoformat()
    past_sections = [(d, s) for d, s in date_sections if d <= today]
    if not past_sections:
        return []
    latest_date = max(d for d, _ in past_sections)
    date_sections = past_sections
    latest_sections = [s for d, s in date_sections if d == latest_date]

    machines = []
    for section in latest_sections:
        for item in section.find_all("li", class_="unit"):
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
            name_tag = link.find("p", class_="title")
            if not name_tag:
                continue
            machines.append({
                "id": id_match.group(1),
                "name": name_tag.get_text(strip=True),
                "releaseDate": latest_date,
            })

    return machines


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


def fetch_borders(page, machine_id):
    """機種IDからボーダー情報を取得する"""
    url = MACHINE_URL.format(id=machine_id)
    try:
        html = fetch_machine_html(page, url, machine_id)
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
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

    return list(best.values())


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

        results = []
        for i, machine in enumerate(machines, 1):
            print(f"[{i}/{len(machines)}] {machine['name']} のボーダー情報を取得中...")
            borders = fetch_borders(page, machine["id"])
            results.append({
                "name": machine["name"],
                "releaseDate": machine["releaseDate"],
                "borders": borders,
            })

        browser.close()

    # いずれの機種もボーダー情報が取得できなかった場合はJSONを更新しない
    if not any(r["borders"] for r in results):
        print("ボーダー情報が取得できませんでした。JSONを更新せずに終了します")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"{OUTPUT_FILE} を更新しました（{len(results)}件）")


if __name__ == "__main__":
    main()
