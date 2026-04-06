import os
import re
import time
import random
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

notified = {}

WEBHOOK_URL = os.environ["WEBHOOK_URL"]

MIN_INTERVAL = 2
MAX_INTERVAL = 4
MENTION_EVERYONE = True

TARGETS = [
    {
        "title": "IVE WORLD TOUR <SHOW WHAT I AM> IN TAIPEI",
        "date_text": "2026/09/11（五）19:00",
        "venue": "台北小巨蛋",
        "url": "https://tixcraft.com/ticket/area/26_ive/22286",
    },
    {
        "title": "IVE WORLD TOUR <SHOW WHAT I AM> IN TAIPEI",
        "date_text": "2026/09/12（六）18:00",
        "venue": "台北小巨蛋",
        "url": "https://tixcraft.com/ticket/area/26_ive/22287",
    },
]

KEYWORDS = []


def send_discord(message: str, mention_everyone: bool = False):
    content = message
    if mention_everyone:
        content = "@everyone\n" + message

    payload = {
        "content": content,
        "allowed_mentions": {
            "parse": ["everyone"]
        }
    }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=10
    )
    response.raise_for_status()
    print("Discord 狀態碼：", response.status_code)


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def keyword_match(text: str) -> bool:
    if not KEYWORDS:
        return True
    return any(keyword in text for keyword in KEYWORDS)


def dedupe_items(items: list[dict]) -> list[dict]:
    seen = set()
    result = []

    for item in items:
        key = (item["label"], item["status"], item["count"])
        if key not in seen:
            seen.add(key)
            result.append(item)

    return result


def check_page(page, target: dict) -> list[dict]:
    print(f"檢查中：{target['title']} / {target['date_text']}")

    page.goto(target["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    items = []

    zones = page.locator("ul.area-list li")
    count = zones.count()

    for i in range(count):
        li = zones.nth(i)
        line = normalize_line(li.inner_text())

        if not line:
            continue

        if any(word in line for word in ["已售完", "Sold out", "sold out", "SOLD OUT", "完售"]):
            continue

        m = re.search(r"(.+?)\s*剩餘\s*(\d+)(?:\s*張)?", line)
        if m:
            label = normalize_line(m.group(1))
            count_num = int(m.group(2))

            if keyword_match(label):
                items.append({
                    "label": label,
                    "status": "count",
                    "count": count_num
                })
            continue

        if "熱賣中" in line:
            label = normalize_line(line.replace("熱賣中", ""))

            if keyword_match(label):
                items.append({
                    "label": label,
                    "status": "hot",
                    "count": None
                })
            continue

        if "區" in line and any(ch.isdigit() for ch in line):
            if keyword_match(line):
                items.append({
                    "label": line,
                    "status": "available",
                    "count": None
                })

    items = dedupe_items(items)
    print("抓到的結果：", items)
    return items


def format_ticket_message(target: dict, ticket_info: list[dict]) -> str:
    now_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    lines = [
        f"活動：{target['date_text']} < {target['venue']} >",
        target["title"],
        "",
        target["url"],
    ]

    if ticket_info:
        lines.append("")
        for item in ticket_info:
            if item["status"] == "count":
                lines.append(f"• {item['label']} | 剩 {item['count']} 張")
            elif item["status"] == "hot":
                lines.append(f"• {item['label']} | 熱賣中")
            else:
                lines.append(f"• {item['label']} | 可購買")
    else:
        lines.append("")
        lines.append("❌ 目前無可選票區")

    lines.append("")
    lines.append(f"查詢時間：{now_time}")

    return "\n".join(lines)


def main():
    print("🚀 bot started")

    for target in TARGETS:
        notified[target["url"]] = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            while True:
                try:
                    for target in TARGETS:
                        url = target["url"]
                        current_ticket_info = check_page(page, target)

                        if current_ticket_info and not notified[url]:
                            msg = format_ticket_message(target, current_ticket_info)
                            send_discord(msg, mention_everyone=MENTION_EVERYONE)
                            notified[url] = True
                            print(f"[已通知] {target['title']} / {target['date_text']}")

                        elif not current_ticket_info:
                            if notified[url]:
                                print(f"[已重置] {target['title']} / {target['date_text']} 目前沒票，等待下次重新通知")
                            notified[url] = False

                        else:
                            print(f"[略過] {target['title']} / {target['date_text']} 仍然有票，但已通知過")

                    sleep_seconds = random.uniform(MIN_INTERVAL, MAX_INTERVAL)
                    print(f"等待 {sleep_seconds:.2f} 秒後再次檢查...\n")
                    time.sleep(sleep_seconds)

                except Exception as e:
                    print("發生錯誤：", e)
                    print("30 秒後重試...\n")
                    time.sleep(30)

        except KeyboardInterrupt:
            print("🛑 Bot 已停止")

        finally:
            browser.close()


if __name__ == "__main__":
    main()