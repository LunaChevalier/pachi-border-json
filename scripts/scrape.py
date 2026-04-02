import json
import re
import time

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.p-world.co.jp"
CALENDAR_URL = f"{BASE_URL}/database/machine/introduce_calendar.cgi"
MACHINE_URL = f"{BASE_URL}/machine/database/{{id}}"
OUTPUT_FILE = "suggestions.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

RATE_MAP = {
    "25玉": "YEN_4",
    "1円": "YEN_1",
}


def fetch_calendar():
    """新台カレンダーから機種名・リリース日・機種IDを取得する"""
    response = requests.get(CALENDAR_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding

    soup = BeautifulSoup(response.text, "html.parser")
    machines = []
    current_date = None

    for tag in soup.find_all(["h2", "a"]):
        if tag.name == "h2":
            text = tag.get_text(strip=True)
            # 例: "2026/04/06 新台予定"
            match = re.search(r"(\d{4})/(\d{2})/(\d{2})", text)
            if match:
                current_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        elif tag.name == "a" and current_date:
            href = tag.get("href", "")
            # 例: /machine/database/12345
            match = re.match(r"/machine/database/(\d+)", href)
            if match:
                machine_id = match.group(1)
                name = tag.get_text(strip=True)
                if name:
                    machines.append({
                        "id": machine_id,
                        "name": name,
                        "releaseDate": current_date,
                    })

    return machines


def fetch_borders(machine_id):
    """機種IDからボーダー情報を取得する"""
    url = MACHINE_URL.format(id=machine_id)
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
    except requests.RequestException:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    borders = []

    # ボーダーテーブルを探す
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["th", "td"])
            if not cells:
                continue

            row_text = " ".join(c.get_text(strip=True) for c in cells)

            for keyword, rate in RATE_MAP.items():
                if keyword in row_text:
                    # 数値を含むセルを探す
                    for cell in cells:
                        text = cell.get_text(strip=True)
                        # ボーダー回転数（小数点あり）を取得
                        match = re.search(r"(\d+\.\d+)", text)
                        if match:
                            baseline_value = float(match.group(1))
                            # 同じrateの重複を避ける
                            if not any(b["rate"] == rate for b in borders):
                                borders.append({
                                    "rate": rate,
                                    "baselineValue": baseline_value,
                                })
                            break

    return borders


def main():
    print("新台カレンダーを取得中...")
    machines = fetch_calendar()
    print(f"{len(machines)}件の機種を取得しました")

    results = []
    for i, machine in enumerate(machines, 1):
        print(f"[{i}/{len(machines)}] {machine['name']} のボーダー情報を取得中...")
        borders = fetch_borders(machine["id"])
        results.append({
            "name": machine["name"],
            "releaseDate": machine["releaseDate"],
            "borders": borders,
        })
        time.sleep(1)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"{OUTPUT_FILE} を更新しました（{len(results)}件）")


if __name__ == "__main__":
    main()
