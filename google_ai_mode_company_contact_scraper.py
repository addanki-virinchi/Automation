"""
Google AI Mode Company Contact Scraper
======================================
Just edit the CONFIG section below, then run:
    python google_ai_mode_company_contact_scraper.py

Requirements:
    pip install undetected-chromedriver selenium pandas
"""

import time
import random
import re
import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

# ══════════════════════════════════════════════════════
#  ✏️  EDIT HERE — all settings in one place
# ══════════════════════════════════════════════════════

INPUT_FILE     = "naukri_jobs_py.csv"       # your input CSV file name
OUTPUT_FILE    = "results.csv"         # output CSV file name
COMPANY_COLUMN = "Company"             # column name in your CSV

CHROME_VERSION = 146                   # your Chrome major version

MAX_EMAILS     = 5                     # max emails to extract per company
WAIT_AI_MODE   = 6                     # seconds to wait after clicking AI Mode
DELAY_MIN      = 4                     # min seconds between companies
DELAY_MAX      = 8                     # max seconds between companies

# ══════════════════════════════════════════════════════

SEARCH_TEMPLATE = "{company} official website email contact"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
JUNK_DOMAINS = {
    "example.com", "sentry.io", "yourdomain.com", "domain.com",
    "wixpress.com", "company.com", "email.com"
}


# ── Helpers ────────────────────────────────────────────────────────────────

def clean_emails(raw):
    seen, out = set(), []
    for e in raw:
        e = e.lower()
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


def try_click_ai_mode(driver):
    """Click AI Mode tab if present. Returns True if clicked."""
    try:
        ai_btn = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//*[contains(@class,"R1QWuf") and contains(text(),"AI Mode")]')
            )
        )
        ai_btn.click()
        time.sleep(WAIT_AI_MODE)
        return True
    except Exception:
        return False


def empty_row(company):
    row = {"company": company, "website": "", "linkedin": "", "notes": "error"}
    for i in range(1, MAX_EMAILS + 1):
        row[f"email_{i}"] = ""
    return row


def scrape_company(driver, company):
    print(f"\n🔍 Searching: {company}")
    driver.get("https://www.google.com")
    time.sleep(1.5)

    try:
        box = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        box.clear()
        box.send_keys(SEARCH_TEMPLATE.format(company=company))
        box.send_keys(Keys.RETURN)
        time.sleep(2)
    except Exception as e:
        print(f"  ⚠ Search box error: {e}")
        return empty_row(company)

    ai_used = try_click_ai_mode(driver)
    print(f"  AI Mode: {'✅ clicked' if ai_used else '⬜ not available — using normal results'}")

    if ai_used:
        time.sleep(WAIT_AI_MODE)

    page_text = driver.find_element(By.TAG_NAME, "body").text
    data = extract_from_text(page_text)

    # Fallback: scan anchor tags for website / linkedin
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
    result = {"company": company, "website": data["website"], "linkedin": data["linkedin"]}
    for i, email in enumerate(emails_padded, 1):
        result[f"email_{i}"] = email
    result["notes"] = f"AI Mode used: {ai_used}"

    print(f"  🌐 {data['website']  or 'not found'}")
    print(f"  📧 {', '.join(data['emails']) or 'none found'}")
    print(f"  🔗 {data['linkedin'] or 'not found'}")
    return result


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    df = pd.read_csv(INPUT_FILE, dtype=str)
    df.dropna(how="all", inplace=True)
    companies = df[COMPANY_COLUMN].dropna().str.strip().tolist()
    print(f"📋 {len(companies)} companies found in '{INPUT_FILE}' → column '{COMPANY_COLUMN}'\n")

    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # options.add_argument("--headless=new")  # uncomment to run without opening a window

    driver = uc.Chrome(options=options, version_main=CHROME_VERSION)
    driver.maximize_window()

    results = []
    try:
        for company in companies:
            if not company:
                continue
            results.append(scrape_company(driver, company))
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
    finally:
        driver.quit()

    df_results = pd.DataFrame(results)
    df.rename(columns={COMPANY_COLUMN: "company"}, inplace=True)
    df["company"] = df["company"].str.strip()

    email_cols = [f"email_{i}" for i in range(1, MAX_EMAILS + 1)]
    new_cols   = ["website", "linkedin"] + email_cols + ["notes"]
    df_final   = df.merge(df_results[["company"] + new_cols], on="company", how="left")

    df_final.to_csv(OUTPUT_FILE, index=False)
    print(f"\n✅ Done! Saved to: {OUTPUT_FILE}")
    print(f"   {len(df_final)} rows | {df_final['website'].fillna('').astype(bool).sum()} websites found")
    print(f"   {df_final['email_1'].fillna('').astype(bool).sum()} companies had at least 1 email extracted")


if __name__ == "__main__":
    main()
