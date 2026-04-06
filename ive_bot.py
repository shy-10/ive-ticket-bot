import os
import re
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright


# =========================
# 1. 基本設定
# =========================

WEBHOOK_URL = os.environ["WEBHOOK_URL"]

CHECK_INTERVAL = 10  # 每幾秒檢查一次
MENTION_EVERYONE = True  # 有票時是否 @everyone

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

# 若只想看特定票價 / 關鍵字，可填：
# 例如：["VIP", "7800", "5800"]
# 全部都看就留空 []
KEYWORDS = []

# 記錄每個網址是否已通知過
notified = {}


# =========================
# 2. Discord 發送
# =========================

def send_discord(message: str, mention_everyone: bool = False):
    try:
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
        print("Discord 狀態碼：", response.status_code)
    except Exception as e:
        print("Discord 發送失敗：", e)


# =========================
# 3. 工具函式
# =========================

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


# =========================
# 4. HTML 結構版：檢查單一頁面
# =========================

def check_page(page, target: dict) -> list[dict]:
    print(f"檢查中：{target['title']} / {target['date_text']}")

    page.goto(target["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    items = []

    # 拓元票區清單
    zones = page.locator("ul.area-list li")
    count = zones.count()

    for i in range(count):
        li = zones.nth(i)
        line = normalize_line(li.inner_text())

        if not line:
            continue

        # 排除售完
        if any(word in line for word in ["已售完", "Sold out", "sold out", "SOLD OUT", "完售"]):
            continue

        # 抓「剩餘 X」或「剩餘 X 張」
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

        # 抓「熱賣中」
        if "熱賣中" in line:
            label = normalize_line(line.replace("熱賣中", ""))

            if keyword_match(label):
                items.append({
                    "label": label,
                    "status": "hot",
                    "count": None
                })
            continue

        # 一般可買
        if "區" in line:
            if keyword_match(line):
                items.append({
                    "label": line,
                    "status": "available",
                    "count": None
                })

    items = dedupe_items(items)
    print("抓到的結果：", items)
    return items


# =========================
# 5. 整理 Discord 訊息排版
# =========================

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


# =========================
# 6. 主程式
# =========================

def main():
    global notified

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

                        # 有票，且尚未通知過 -> 發一次
                        if current_ticket_info and not notified[url]:
                            msg = format_ticket_message(target, current_ticket_info)
                            send_discord(msg, mention_everyone=MENTION_EVERYONE)
                            notified[url] = True
                            print(f"[已通知] {target['title']} / {target['date_text']}")

                        # 沒票 -> 重置通知狀態
                        elif not current_ticket_info:
                            if notified[url]:
                                print(f"[已重置] {target['title']} / {target['date_text']} 目前沒票，等待下次重新通知")
                            notified[url] = False

                        # 還有票但已通知過 -> 不重複發
                        else:
                            print(f"[略過] {target['title']} / {target['date_text']} 仍然有票，但已通知過")

                    print(f"等待 {CHECK_INTERVAL} 秒後再次檢查...\n")
                    time.sleep(CHECK_INTERVAL)

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