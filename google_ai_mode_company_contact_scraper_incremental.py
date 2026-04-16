"""
Google AI Mode Scraper — Incremental + Dedup + Captcha Alert
=============================================================
Edit the CONFIG section, then run:
    python google_ai_mode_company_contact_scraper_incremental.py

Requirements:
    pip install undetected-chromedriver selenium pandas playsound
"""

import time
import random
import re
import os
import sys
import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

# ══════════════════════════════════════════════════════
#  ✏️  EDIT HERE — all settings in one place
# ══════════════════════════════════════════════════════

INPUT_FILE     = "naukri_jobs_py.csv"       # input CSV
OUTPUT_FILE    = "results.csv"         # output CSV (incremental — appended live)
COMPANY_COLUMN = "Company"             # column name in input CSV

CHROME_VERSION = 146                   # Chrome major version

MAX_EMAILS     = 5                     # max emails per company
WAIT_AI_MODE   = 6                     # seconds to wait after AI Mode loads
DELAY_MIN      = 4                     # min delay between searches (seconds)
DELAY_MAX      = 8                     # max delay between searches (seconds)

ALERT_SOUND    = "alert.mp3"           # path to your alert MP3/MP4

CAPTCHA_CHECK_INTERVAL = 2             # how often to poll for captcha resolution
CAPTCHA_WAIT_TIMEOUT   = 300           # max seconds to wait for user to solve captcha
SAVE_EVERY     = 5                     # save progress every N companies

# ══════════════════════════════════════════════════════

SEARCH_TEMPLATE = "{company} official website link and email contact for recruitment"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
JUNK_DOMAINS = {
    "example.com", "sentry.io", "yourdomain.com", "domain.com",
    "wixpress.com", "company.com", "email.com", "yoursite.com",
}

CAPTCHA_SIGNALS = [
    "our systems have detected unusual traffic",
    "please solve this captcha",
    "verify you're not a robot",
    "captcha",
    "recaptcha",
    "before you continue",
    "unusual traffic from your computer network",
    "why did this happen",
]


# ── Logging ────────────────────────────────────────────────────────────────

LOG_FILE = OUTPUT_FILE.replace(".csv", "_run.log")

def log(msg: str):
    """Print to console and append to log file."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── Incremental CSV ────────────────────────────────────────────────────────

def load_already_done() -> set:
    """
    Load company names already present in the output CSV.
    Normalised to lowercase+stripped for comparison.
    """
    if not os.path.exists(OUTPUT_FILE):
        return set()
    try:
        df = pd.read_csv(OUTPUT_FILE, dtype=str)
        if "company" in df.columns:
            return set(df["company"].dropna().str.strip().str.lower())
    except Exception:
        pass
    return set()


def append_row(row: dict):
    """Append a single result row to the output CSV (creates file+header if needed)."""
    df_row = pd.DataFrame([row])
    write_header = not os.path.exists(OUTPUT_FILE)
    df_row.to_csv(OUTPUT_FILE, mode="a", index=False, header=write_header)


# ── Sound Alert ────────────────────────────────────────────────────────────

def play_alert():
    sound_path = os.path.abspath(ALERT_SOUND)
    if not os.path.exists(sound_path):
        log(f"  ⚠ Sound file not found: {sound_path}")
        return
    try:
        from playsound import playsound
        playsound(sound_path, block=False)
        return
    except Exception:
        pass
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    elif sys.platform == "darwin":
        os.system(f'afplay "{sound_path}" &')
    else:
        for player in ["mpg123", "mpg321", "ffplay", "aplay"]:
            if os.system(f"which {player} > /dev/null 2>&1") == 0:
                os.system(f'{player} -q "{sound_path}" &')
                break


# ── Captcha ────────────────────────────────────────────────────────────────

def is_captcha_page(driver) -> bool:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        return any(sig in body for sig in CAPTCHA_SIGNALS)
    except Exception:
        return False


def wait_for_captcha_solve(driver) -> bool:
    log("🚨 CAPTCHA DETECTED — solve it in the browser. Script paused.")
    play_alert()
    elapsed = alert_timer = 0
    while elapsed < CAPTCHA_WAIT_TIMEOUT:
        time.sleep(CAPTCHA_CHECK_INTERVAL)
        elapsed     += CAPTCHA_CHECK_INTERVAL
        alert_timer += CAPTCHA_CHECK_INTERVAL
        if alert_timer >= 10:
            play_alert()
            alert_timer = 0
        if not is_captcha_page(driver):
            log("✅ Captcha solved — resuming.")
            time.sleep(2)
            return True
    log("❌ Captcha timeout. Saving progress and stopping.")
    return False


# ── Helpers ────────────────────────────────────────────────────────────────

def clean_emails(raw):
    seen, out = set(), []
    for e in raw:
        e = e.lower().strip()
        domain = e.split("@")[-1]
        if e not in seen and domain not in JUNK_DOMAINS and not domain.endswith(".png"):
            seen.add(e)
            out.append(e)
        if len(out) >= MAX_EMAILS:
            break
    return out


def extract_from_text(text):
    emails   = EMAIL_RE.findall(text)
    website  = re.search(r'https?://(?!.*google|.*linkedin)[^\s"\'<>]+', text)
    linkedin = re.search(r'https?://(?:www\.)?linkedin\.com/company/[^\s"\'<>]+', text)
    return {
        "emails":   clean_emails(emails),
        "website":  website.group(0).rstrip("/.,")  if website  else "",
        "linkedin": linkedin.group(0).rstrip("/.,") if linkedin else "",
    }


def try_click_ai_mode(driver) -> bool:
    try:
        btn = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//*[contains(@class,"R1QWuf") and contains(text(),"AI Mode")]')
            )
        )
        btn.click()
        time.sleep(WAIT_AI_MODE)
        return True
    except Exception:
        return False


def empty_row(company, note="error"):
    row = {
        "company": company, "website": "", "linkedin": "",
        "status": "error", "notes": note,
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for i in range(1, MAX_EMAILS + 1):
        row[f"email_{i}"] = ""
    return row


# ── Core scrape ────────────────────────────────────────────────────────────

def scrape_company(driver, company) -> dict | None:
    """Returns result dict, or None to signal a hard stop."""
    log(f"🔍 Searching: {company}")
    driver.get("https://www.google.com")
    time.sleep(1.5)

    if is_captcha_page(driver):
        if not wait_for_captcha_solve(driver):
            return None

    try:
        box = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        box.clear()
        box.send_keys(SEARCH_TEMPLATE.format(company=company))
        box.send_keys(Keys.RETURN)
        time.sleep(2)
    except Exception as e:
        log(f"  ⚠ Search box error: {e}")
        return empty_row(company)

    if is_captcha_page(driver):
        if not wait_for_captcha_solve(driver):
            return None
        try:
            box = driver.find_element(By.NAME, "q")
            box.clear()
            box.send_keys(SEARCH_TEMPLATE.format(company=company))
            box.send_keys(Keys.RETURN)
            time.sleep(2)
        except Exception:
            return empty_row(company, note="captcha solved, re-search failed")

    ai_used = try_click_ai_mode(driver)
    log(f"  AI Mode: {'✅' if ai_used else '⬜ not available'}")
    if ai_used:
        time.sleep(WAIT_AI_MODE)

    page_text = driver.find_element(By.TAG_NAME, "body").text
    data = extract_from_text(page_text)

    # Anchor tag fallback for website / linkedin
    if not data["website"] or not data["linkedin"]:
        skip = ["google.", "linkedin.", "facebook.", "twitter.", "youtube.", "wikipedia."]
        for link in driver.find_elements(By.TAG_NAME, "a"):
            href = link.get_attribute("href") or ""
            if not data["linkedin"] and "linkedin.com/company/" in href:
                data["linkedin"] = href.split("?")[0]
            if not data["website"] and href.startswith("http") and not any(s in href for s in skip):
                if company.lower().replace(" ", "") in href.lower():
                    data["website"] = href.split("?")[0]

    emails_padded = data["emails"] + [""] * (MAX_EMAILS - len(data["emails"]))
    result = {
        "company":    company,
        "website":    data["website"],
        "linkedin":   data["linkedin"],
        "status":     "ok" if (data["website"] or data["emails"]) else "no_data",
        "notes":      f"AI Mode: {ai_used}",
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for i, email in enumerate(emails_padded, 1):
        result[f"email_{i}"] = email

    log(f"  🌐 {data['website'] or 'not found'} | "
        f"📧 {', '.join(data['emails']) or 'none'} | "
        f"🔗 {data['linkedin'] or 'not found'}")
    return result


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    log("=" * 55)
    log(f"▶ Starting scraper | Input: {INPUT_FILE} | Output: {OUTPUT_FILE}")

    # Read input
    df = pd.read_csv(INPUT_FILE, dtype=str)
    df.dropna(how="all", inplace=True)
    all_companies = df[COMPANY_COLUMN].dropna().str.strip().tolist()

    # ── Deduplication ──────────────────────────────────
    # 1. Deduplicate within the input CSV itself
    seen_in_input   = set()
    unique_companies = []
    duplicates_in_input = []
    for c in all_companies:
        key = c.lower()
        if key not in seen_in_input:
            seen_in_input.add(key)
            unique_companies.append(c)
        else:
            duplicates_in_input.append(c)

    if duplicates_in_input:
        log(f"  ℹ Duplicates found in input CSV (skipped): {duplicates_in_input}")

    # 2. Skip companies already in the output CSV (incremental resume)
    already_done = load_already_done()
    to_scrape = [c for c in unique_companies if c.lower() not in already_done]
    skipped   = len(unique_companies) - len(to_scrape)

    log(f"📋 Total in CSV      : {len(all_companies)}")
    log(f"   Unique companies  : {len(unique_companies)}")
    log(f"   Already scraped   : {skipped}  (loaded from {OUTPUT_FILE})")
    log(f"   To scrape now     : {len(to_scrape)}")
    log(f"🔊 Alert sound       : {os.path.abspath(ALERT_SOUND)}")
    log("=" * 55)

    if not to_scrape:
        log("✅ Nothing new to scrape. All companies already done!")
        return

    # Launch browser
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # options.add_argument("--headless=new")  # ← keep commented — captcha needs visible browser

    driver = uc.Chrome(options=options, version_main=CHROME_VERSION)
    driver.maximize_window()

    success = error = 0
    try:
        for idx, company in enumerate(to_scrape, 1):
            log(f"\n[{idx}/{len(to_scrape)}]")
            row = scrape_company(driver, company)

            if row is None:
                log("⛔ Hard stop — captcha timed out.")
                break

            # Write row immediately to CSV (incremental)
            append_row(row)

            if row["status"] == "ok":
                success += 1
            else:
                error += 1

            if idx % SAVE_EVERY == 0:
                log(f"💾 Progress checkpoint: {idx}/{len(to_scrape)} done")

            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    finally:
        driver.quit()

    log("=" * 55)
    log(f"✅ Session complete → {OUTPUT_FILE}")
    log(f"   Scraped: {success + error} | ✅ OK: {success} | ⚠ No data: {error}")
    log(f"   Log saved to: {LOG_FILE}")
    log("=" * 55)


if __name__ == "__main__":
    main()
