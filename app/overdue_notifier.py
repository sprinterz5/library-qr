"""
Due Date Reminder — скачивает Issued Books XLSX из eLibra,
находит книги с ≤1 день до возврата и шлёт email-напоминания.
"""
_DEV_SIGNATURE = "AB2025"

import asyncio
import logging
import os
import smtplib
import tempfile
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Dict, Optional

import openpyxl

from app.core.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM_EMAIL, SMTP_USE_TLS, TEST_EMAIL,
)
from app.core.utils import logger as app_logger

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. Download XLSX via Playwright
# ──────────────────────────────────────────────

async def download_issued_books_xlsx() -> str:
    """
    Use the existing Playwright RPA instance to navigate to the Statistics page,
    find Reading room → Issued books, and click the Export button.
    Returns path to the downloaded XLSX file.
    """
    from app.core.rpa import rpa  # lazy import to avoid circular
    from app.rpa_elibra import BASE_URL

    STATISTICS_URL = f"{BASE_URL}/workspace/statistics"

    await rpa._ensure_initialized()
    async with rpa._lock:
        page = rpa.page
        await rpa._ensure_page()

        # 1. Navigate to statistics page
        logger.info(f"[overdue] Navigating to {STATISTICS_URL}")
        await page.goto(STATISTICS_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Auto-login if needed
        if "/auth/login" in page.url:
            logger.info("[overdue] Login required, attempting auto-login")
            await rpa._auto_login_if_needed()
            await page.goto(STATISTICS_URL, wait_until="domcontentloaded")
            await asyncio.sleep(3)

        logger.info(f"[overdue] On page: {page.url}")

        # 2. Find and expand "Reading room" collapse panel
        reading_room_selectors = [
            "text=Reading room",
            ".ant-collapse-header:has-text('Reading room')",
            "span:has-text('Reading room')",
        ]
        clicked_reading_room = False
        for sel in reading_room_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.click()
                    logger.info(f"[overdue] Clicked Reading room via: {sel}")
                    clicked_reading_room = True
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                continue

        if not clicked_reading_room:
            debug_path = os.path.join(tempfile.gettempdir(), "elibra_debug_reading_room.png")
            await page.screenshot(path=debug_path, full_page=True)
            logger.error(f"[overdue] 'Reading room' not found. Screenshot: {debug_path}")
            try:
                body_text = await page.locator("body").inner_text()
                logger.error(f"[overdue] Page text (first 2000): {body_text[:2000]}")
            except Exception:
                pass
            raise RuntimeError(f"'Reading room' not found on statistics page. Screenshot: {debug_path}")

        # 3. Click "Issued books" link inside the expanded panel
        issued_selectors = [
            "text=Issued books",
            "a:has-text('Issued books')",
            "span:has-text('Issued books')",
        ]
        clicked_issued = False
        for sel in issued_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.click()
                    logger.info(f"[overdue] Clicked 'Issued books' via: {sel}")
                    clicked_issued = True
                    await asyncio.sleep(4)  # wait for table to load
                    break
            except Exception:
                continue

        if not clicked_issued:
            debug_path = os.path.join(tempfile.gettempdir(), "elibra_debug_issued.png")
            await page.screenshot(path=debug_path, full_page=True)
            logger.error(f"[overdue] 'Issued books' not found. Screenshot: {debug_path}")
            raise RuntimeError(f"'Issued books' not found. Screenshot: {debug_path}")

        logger.info(f"[overdue] On Issued Books page: {page.url}")

        # 4. A "Report Parameters" modal appears — click "Generate" to download
        await asyncio.sleep(2)  # wait for modal to appear

        generate_selectors = [
            "button:has-text('Generate')",
            ".ant-modal button.ant-btn-primary",
            ".ant-modal-footer button:has-text('Generate')",
        ]
        generate_btn = None
        for sel in generate_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=5000):
                    generate_btn = el
                    logger.info(f"[overdue] Found Generate button via: {sel}")
                    break
            except Exception:
                continue

        if not generate_btn:
            debug_path = os.path.join(tempfile.gettempdir(), "elibra_debug_generate.png")
            await page.screenshot(path=debug_path)
            logger.error(f"[overdue] Generate button not found. Screenshot: {debug_path}")
            raise RuntimeError(f"Generate button not found. Screenshot: {debug_path}")

        # 5. Click Generate and wait for download
        async with page.expect_download(timeout=60000) as download_info:
            await generate_btn.click()

        download = await download_info.value

        # Save to temp file
        tmp_dir = tempfile.mkdtemp(prefix="elibra_export_")
        dest = os.path.join(tmp_dir, download.suggested_filename or "issued_books.xlsx")
        await download.save_as(dest)
        logger.info(f"[overdue] Downloaded XLSX to: {dest}")
        return dest


# ──────────────────────────────────────────────
# 2. Parse XLSX — find books due soon
# ──────────────────────────────────────────────

def parse_due_soon(filepath: str, days_threshold: int = 1) -> List[Dict]:
    """
    Read the XLSX file exported from eLibra Issued Books.
    Headers are in row 2:
      C = Наименование (Title)
      G = Имя (First Name)
      H = Фамилия (Last Name)
      I = Email
      K = Дата возврата (Due Date)

    Returns records where due_date - today <= days_threshold.
    """
    wb = openpyxl.load_workbook(filepath)
    ws = wb.active

    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    threshold = today + timedelta(days=days_threshold + 1)  # end of "threshold" day

    results = []
    for row in ws.iter_rows(min_row=3):  # data starts at row 3
        # Columns: A=0, B=1, C=2(title), ..., G=6(name), H=7(surname), I=8(email), J=9, K=10(due)
        cells = [c.value for c in row]
        if len(cells) < 11:
            continue

        title = cells[2]       # C — Наименование
        first_name = cells[6]  # G — Имя
        last_name = cells[7]   # H — Фамилия
        email = cells[8]       # I — Email
        due_raw = cells[10]    # K — Дата возврата

        if not email or not due_raw:
            continue

        # Parse due date
        if isinstance(due_raw, datetime):
            due_date = due_raw
        elif isinstance(due_raw, str):
            try:
                due_date = datetime.fromisoformat(due_raw.replace(" ", "T"))
            except ValueError:
                logger.warning(f"Cannot parse due date: {due_raw}")
                continue
        else:
            continue

        # Filter: due_date <= today + threshold (book is due within N days or already overdue)
        if due_date <= threshold:
            days_left = (due_date.replace(hour=0, minute=0, second=0, microsecond=0) - today).days
            results.append({
                "title": title or "Unknown",
                "first_name": first_name or "",
                "last_name": last_name or "",
                "email": str(email).strip(),
                "due_date": due_date,
                "days_left": days_left,
            })

    wb.close()
    logger.info(f"Found {len(results)} books due within {days_threshold} day(s)")
    return results


# ──────────────────────────────────────────────
# 3. Send reminder email
# ──────────────────────────────────────────────

def _build_email_html(record: Dict) -> str:
    name = f"{record['first_name']} {record['last_name']}".strip() or "Reader"
    title = record["title"]
    due = record["due_date"].strftime("%d.%m.%Y")
    days_left = record["days_left"]

    if days_left < 0:
        status_line = f"⚠️ Книга просрочена на {abs(days_left)} дн."
        status_color = "#e74c3c"
    elif days_left == 0:
        status_line = "⚠️ Сегодня последний день возврата!"
        status_color = "#e67e22"
    else:
        status_line = f"📅 До возврата осталось: {days_left} дн."
        status_color = "#f39c12"

    return f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a365d; color: white; padding: 20px 30px; border-radius: 8px 8px 0 0;">
            <h2 style="margin: 0;">📚 Coventry University Library</h2>
            <p style="margin: 5px 0 0; opacity: 0.85;">ТЕСТ ФУНКЦИИ: Напоминание о возврате книги</p>
        </div>
        <div style="background: #ffffff; padding: 25px 30px; border: 1px solid #e2e8f0;">
            <p>Здравствуйте, <strong>{name}</strong>! Это тестовое сообщение.</p>
            <div style="background: #f7fafc; border-left: 4px solid {status_color}; padding: 15px; margin: 15px 0; border-radius: 0 4px 4px 0;">
                <p style="margin: 0; font-size: 16px; color: {status_color}; font-weight: bold;">{status_line}</p>
            </div>
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
                <tr>
                    <td style="padding: 8px 0; color: #718096; width: 120px;">Книга:</td>
                    <td style="padding: 8px 0; font-weight: bold;">{title}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #718096;">Дата возврата:</td>
                    <td style="padding: 8px 0; font-weight: bold;">{due}</td>
                </tr>
            </table>
            <p style="color: #4a5568;">Пожалуйста, верните книгу в библиотеку вовремя. В случае вопросов обращайтесь по адресу <a href="mailto:library@coventry.edu.kz">library@coventry.edu.kz</a>.</p>
        </div>
        <div style="background: #f7fafc; padding: 15px 30px; border-radius: 0 0 8px 8px; border: 1px solid #e2e8f0; border-top: 0; text-align: center; color: #a0aec0; font-size: 12px;">
            Coventry University • Library • Astana, Kazakhstan
        </div>
    </div>
    """


def send_reminder_email(record: Dict) -> bool:
    """Send a single reminder email. Returns True on success."""
    if not SMTP_HOST or not SMTP_USER:
        logger.error("SMTP not configured — cannot send email")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = record["email"]

    days_left = record["days_left"]
    if days_left < 0:
        msg["Subject"] = f"⚠️ Просроченная книга: {record['title']}"
    else:
        msg["Subject"] = f"📚 Напоминание: верните «{record['title']}» до {record['due_date'].strftime('%d.%m.%Y')}"

    html_body = _build_email_html(record)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)

        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, record["email"], msg.as_string())
        server.quit()
        logger.info(f"✉ Email sent to {record['email']} — «{record['title']}»")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {record['email']}: {e}")
        return False


# ──────────────────────────────────────────────
# 4. Orchestrator
# ──────────────────────────────────────────────

async def run_due_reminder(test_mode: bool = False, days_threshold: int = 1) -> Dict:
    """
    Full cycle: download XLSX → parse → send emails.
    test_mode=True → only send to TEST_EMAIL.
    Returns summary dict.
    """
    logger.info(f"=== Due Reminder START (test_mode={test_mode}, threshold={days_threshold} days) ===")

    result = {"ok": False, "downloaded": False, "records_found": 0, "emails_sent": 0, "errors": []}

    # Step 1: Download
    try:
        xlsx_path = await download_issued_books_xlsx()
        result["downloaded"] = True
    except Exception as e:
        msg = f"Failed to download XLSX: {e}"
        logger.error(msg, exc_info=True)
        result["errors"].append(msg)
        return result

    # Step 2: Parse
    try:
        records = parse_due_soon(xlsx_path, days_threshold=days_threshold)
        result["records_found"] = len(records)
    except Exception as e:
        msg = f"Failed to parse XLSX: {e}"
        logger.error(msg, exc_info=True)
        result["errors"].append(msg)
        return result
    finally:
        # Clean up temp file
        try:
            os.remove(xlsx_path)
            os.rmdir(os.path.dirname(xlsx_path))
        except OSError:
            pass

    if not records:
        logger.info("No books due soon — nothing to send.")
        result["ok"] = True
        return result

    # Step 3: Filter in test mode
    if test_mode:
        original_count = len(records)
        records = [r for r in records if r["email"].lower() == TEST_EMAIL.lower()]
        logger.info(f"Test mode: filtered {original_count} → {len(records)} records (only {TEST_EMAIL})")
        result["records_found"] = len(records)
        if not records:
            logger.info(f"No records for {TEST_EMAIL} — nothing to send in test mode.")
            result["ok"] = True
            result["message"] = f"No books due soon for {TEST_EMAIL}"
            return result

    # Step 4: Send emails
    sent = 0
    for rec in records:
        if send_reminder_email(rec):
            sent += 1
        else:
            result["errors"].append(f"Failed to send to {rec['email']}")

    result["emails_sent"] = sent
    result["ok"] = sent > 0 or len(records) == 0
    logger.info(f"=== Due Reminder END: {sent}/{len(records)} emails sent ===")
    return result
