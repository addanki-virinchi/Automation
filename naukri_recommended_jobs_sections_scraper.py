import argparse
import csv
import logging
import os
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


RECOMMENDED_JOBS_URL = "https://www.naukri.com/mnjuser/recommendedjobs"
LOGIN_URL = "https://www.naukri.com/nlogin/login"
DEFAULT_ENV_FILE = ".env"

TAB_ID_TO_SECTION = {
    "apply": "Applies",
    "profile": "Profile",
    "similar_jobs": "You Might Like",
    "preference": "Preferences",
}

TAB_WRAPPER_SELECTOR = "div.tabs-container div.tab-wrapper[id]"
TAB_ACTIVE_MARKER_SELECTOR = ".tab-list-item.tab-list-active, .border-div.tab-list-active"
JOB_CARD_SELECTORS = [
    "article.jobTuple[data-job-id]",
    "article.jobTuple",
    "div.jobTuple[data-job-id]",
    "div.jobTuple",
]

TITLE_SELECTORS = [
    "p.title",
    "p.title[title]",
    "a.title",
    "a.jobTitle",
    "a[title][href*='job-listings']",
    "a[title][href*='job-']",
    "span.title",
    "span[class*='jobTitle']",
]

COMPANY_SELECTORS = [
    "a.subTitle",
    "a.companyName",
    "span.subTitle",
    "span.compName",
    "span.company",
    "span[class*='comp']",
]

LOCATION_SELECTORS = [
    "li.placeHolderLi.location span",
    "li.location span",
    "span.loc",
    "span.location",
    "div.loc",
    "span.locWdth",
    "span[class*='loc']",
]

EXPERIENCE_SELECTORS = [
    "li.placeHolderLi.experience span",
    "li.experience span",
    "span.exp",
    "span.experience",
    "span.expwdth",
    "span[class*='exp']",
]

SALARY_SELECTORS = [
    "li.placeHolderLi.salary span",
    "li.salary span",
    "span.salary",
    "span.sal",
    "span[class*='salary']",
]

POSTED_SELECTORS = [
    ".jobTupleFooter .plcHolder span",
    ".jobTupleFooter .type span",
    ".jobTupleFooter span",
]

DESCRIPTION_SELECTORS = [
    ".job-description span",
    "div.job-description span",
    "div.job-desc span",
]

TAGS_SELECTORS = [
    "ul.tags li",
    "ul.tags span",
    "ul[class*='tag'] li",
]

LOGIN_FIELD_SELECTORS = [
    "input#usernameField",
    "input[name='email']",
    "input[type='text'][placeholder*='Email']",
]

PASSWORD_FIELD_SELECTORS = [
    "input#passwordField",
    "input[type='password']",
]


@dataclass
class RecommendedJobCard:
    section: str
    job_id: str
    title: str
    company: str
    location: str
    experience: str
    salary: str
    posted: str
    tags: List[str]
    description: str
    job_url: str


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s")


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def get_credentials() -> Tuple[str, str]:
    email = os.getenv("NAUKRI_USERNAME") or os.getenv("NAUKRI_EMAIL") or ""
    password = os.getenv("NAUKRI_PASSWORD") or ""
    if not email or not password:
        raise RuntimeError("Missing credentials. Set NAUKRI_USERNAME/NAUKRI_PASSWORD (for example via .env).")
    return email, password


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def find_first(parent, selectors: List[str]):
    for selector in selectors:
        try:
            elements = parent.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for el in elements:
            try:
                if el.is_displayed():
                    return el
            except WebDriverException:
                continue
    return None


def first_text(parent, selectors: List[str]) -> str:
    for selector in selectors:
        try:
            elements = parent.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for el in elements:
            try:
                text = (el.text or "").strip()
            except WebDriverException:
                continue
            if text:
                return text
            try:
                title = (el.get_attribute("title") or "").strip()
            except WebDriverException:
                title = ""
            if title:
                return title
    return ""


def normalize_job_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def safe_get_attribute(el, name: str) -> str:
    try:
        return (el.get_attribute(name) or "").strip()
    except WebDriverException:
        return ""


def is_element_disabled(element) -> bool:
    disabled_attr = safe_get_attribute(element, "disabled").lower()
    aria_disabled = safe_get_attribute(element, "aria-disabled").lower()
    classes = safe_get_attribute(element, "class").lower()
    if disabled_attr in {"true", "disabled"}:
        return True
    if aria_disabled in {"true", "disabled"}:
        return True
    if "disabled" in classes and "not-disabled" not in classes:
        return True
    return False


def robust_click(driver, element, timeout: int = 10) -> None:
    def _clickable(_drv):
        try:
            return element.is_displayed() and element.is_enabled() and not is_element_disabled(element)
        except WebDriverException:
            return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
    except WebDriverException:
        pass

    try:
        WebDriverWait(driver, timeout).until(_clickable)
    except TimeoutException:
        pass

    last_exc: Optional[Exception] = None
    for attempt in range(4):
        try:
            element.click()
            return
        except WebDriverException as exc:
            last_exc = exc
        try:
            ActionChains(driver).move_to_element(element).pause(0.1).click(element).perform()
            return
        except WebDriverException as exc:
            last_exc = exc
        try:
            driver.execute_script(
                "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window}));",
                element,
            )
            return
        except WebDriverException as exc:
            last_exc = exc
        time.sleep(0.35 + (attempt * 0.2))

    if last_exc:
        raise last_exc


def wait_for_document_ready(driver, timeout: int = 20) -> None:
    try:
        WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
    except TimeoutException:
        return


def delay(min_s: float = 1.2, max_s: float = 2.3) -> None:
    time.sleep(random.uniform(min_s, max_s))


def create_driver(headless: bool, user_data_dir: Optional[str], profile_dir: Optional[str]):
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US,en")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,900")
    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
    if profile_dir:
        options.add_argument(f"--profile-directory={profile_dir}")
    driver = webdriver.Chrome(service=Service(), options=options)
    driver.set_page_load_timeout(45)
    return driver


def is_login_form_present(driver) -> bool:
    try:
        return any(driver.find_elements(By.CSS_SELECTOR, sel) for sel in LOGIN_FIELD_SELECTORS)
    except WebDriverException:
        return False


def login_if_needed(driver) -> None:
    if not driver.current_url or "recommendedjobs" not in (driver.current_url or ""):
        logging.info("Navigating to Recommended Jobs: %s", RECOMMENDED_JOBS_URL)
        try:
            driver.get(RECOMMENDED_JOBS_URL)
        except TimeoutException:
            pass
        wait_for_document_ready(driver, timeout=30)
        delay(1.0, 1.6)

    if not is_login_form_present(driver):
        logging.info("Login form not detected. Assuming already logged in.")
        return

    logging.info("Login form detected. Attempting login.")
    email_field = find_first(driver, LOGIN_FIELD_SELECTORS)
    password_field = find_first(driver, PASSWORD_FIELD_SELECTORS)
    if not email_field or not password_field:
        logging.info("Login fields not found on current page; opening login URL.")
        try:
            driver.get(LOGIN_URL)
        except TimeoutException:
            pass
        wait_for_document_ready(driver, timeout=30)
        delay(1.0, 1.6)
        email_field = find_first(driver, LOGIN_FIELD_SELECTORS)
        password_field = find_first(driver, PASSWORD_FIELD_SELECTORS)
    if not email_field or not password_field:
        raise RuntimeError("Login fields not found.")

    email, password = get_credentials()
    try:
        email_field.clear()
    except WebDriverException:
        pass
    email_field.send_keys(email)
    try:
        password_field.clear()
    except WebDriverException:
        pass
    password_field.send_keys(password)

    submit_btn = find_first(driver, ["button[type='submit']", "button.loginButton", "button#loginBtn"])
    if not submit_btn:
        buttons = driver.find_elements(
            By.XPATH,
            "//button[contains(translate(normalize-space(.), 'LOGIN', 'login'), 'login')]",
        )
        submit_btn = buttons[0] if buttons else None
    if not submit_btn:
        password_field.send_keys(Keys.ENTER)
    else:
        robust_click(driver, submit_btn, timeout=10)

    wait_for_document_ready(driver, timeout=30)
    delay(1.6, 2.4)

    if is_login_form_present(driver):
        page_text = (driver.page_source or "").lower()
        if "captcha" in page_text or "verify" in page_text or "access denied" in page_text:
            raise RuntimeError("Login appears blocked by captcha/verification/access denied.")
        logging.warning("Login form still present after submit; continuing anyway.")

    try:
        driver.get(RECOMMENDED_JOBS_URL)
    except TimeoutException:
        pass
    wait_for_document_ready(driver, timeout=30)
    delay(1.0, 1.6)


def get_tabs(driver) -> dict:
    tabs = {}
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, TAB_WRAPPER_SELECTOR)
    except WebDriverException:
        elements = []
    for el in elements:
        tab_id = safe_get_attribute(el, "id")
        if tab_id:
            tabs[tab_id] = el
    return tabs


def wait_for_tabs(driver, timeout: int = 15) -> dict:
    def _ready(drv):
        tabs = get_tabs(drv)
        return len(tabs) > 0

    try:
        WebDriverWait(driver, timeout).until(_ready)
    except TimeoutException:
        return {}
    return get_tabs(driver)


def find_job_cards(driver) -> List[object]:
    for selector in JOB_CARD_SELECTORS:
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            cards = []
        if cards:
            return cards
    return []


def scroll_to_load_all_cards(driver, max_scrolls: int = 18, settle_rounds: int = 2) -> None:
    stable = 0
    seen_ids = set()
    for _ in range(max_scrolls):
        cards = find_job_cards(driver)
        before = len(seen_ids)
        for card in cards:
            job_id = safe_get_attribute(card, "data-job-id")
            if job_id:
                seen_ids.add(job_id)
        if len(seen_ids) == before:
            stable += 1
        else:
            stable = 0
        if stable >= settle_rounds:
            return
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        except WebDriverException:
            return
        delay(0.9, 1.4)


def extract_job_url(card) -> str:
    try:
        links = card.find_elements(By.TAG_NAME, "a")
    except WebDriverException:
        links = []
    for link in links:
        href = normalize_job_url(safe_get_attribute(link, "href"))
        if not href:
            continue
        if "job-listings" in href or "/job-" in href:
            return href
    job_id = safe_get_attribute(card, "data-job-id")
    if job_id:
        return f"https://www.naukri.com/job-listings?jobId={job_id}"
    return ""


def extract_tags(card) -> List[str]:
    tags: List[str] = []
    for selector in TAGS_SELECTORS:
        try:
            elements = card.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for el in elements:
            try:
                text = (el.text or "").strip()
            except WebDriverException:
                continue
            if text and text not in tags:
                tags.append(text)
        if tags:
            return tags
    return tags


def parse_card(card, section: str) -> RecommendedJobCard:
    job_id = safe_get_attribute(card, "data-job-id")
    title = first_text(card, TITLE_SELECTORS)
    company = first_text(card, COMPANY_SELECTORS)
    location = first_text(card, LOCATION_SELECTORS)
    experience = first_text(card, EXPERIENCE_SELECTORS)
    salary = first_text(card, SALARY_SELECTORS)
    posted = first_text(card, POSTED_SELECTORS)
    description = first_text(card, DESCRIPTION_SELECTORS)
    tags = extract_tags(card)
    job_url = extract_job_url(card)
    return RecommendedJobCard(
        section=section,
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        experience=experience,
        salary=salary,
        posted=posted,
        tags=tags,
        description=description,
        job_url=job_url,
    )


def click_tab_and_collect(driver, tab_id: str, tab_el) -> List[RecommendedJobCard]:
    section = TAB_ID_TO_SECTION.get(tab_id, tab_id)
    logging.info("Collecting section: %s", section)

    before_cards = find_job_cards(driver)
    before_first = before_cards[0] if before_cards else None
    before_first_id = safe_get_attribute(before_first, "data-job-id") if before_first else ""

    try:
        robust_click(driver, tab_el, timeout=8)
    except WebDriverException:
        try:
            driver.execute_script("arguments[0].click();", tab_el)
        except WebDriverException:
            pass

    def _tab_active(drv):
        try:
            return bool(tab_el.find_elements(By.CSS_SELECTOR, TAB_ACTIVE_MARKER_SELECTOR))
        except WebDriverException:
            return True

    try:
        WebDriverWait(driver, 10).until(_tab_active)
    except TimeoutException:
        pass

    if before_first is not None:
        try:
            WebDriverWait(driver, 10).until(lambda d: _stale(before_first))
        except TimeoutException:
            pass

    if before_first_id:
        try:
            WebDriverWait(driver, 10).until(
                lambda d: (safe_get_attribute(find_job_cards(d)[0], "data-job-id") if find_job_cards(d) else "") != before_first_id
            )
        except TimeoutException:
            pass

    scroll_to_load_all_cards(driver)
    cards = find_job_cards(driver)
    results: List[RecommendedJobCard] = []
    seen_ids = set()
    for card in cards:
        try:
            parsed = parse_card(card, section)
        except StaleElementReferenceException:
            continue
        key = parsed.job_id or f"{parsed.title}|{parsed.company}|{parsed.location}"
        if key in seen_ids:
            continue
        seen_ids.add(key)
        results.append(parsed)
    logging.info("Section %s: %s cards", section, len(results))
    return results


def _stale(element) -> bool:
    try:
        _ = element.is_enabled()
        return False
    except StaleElementReferenceException:
        return True
    except WebDriverException:
        return False


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract Recommended Jobs cards from Naukri across tabs/sections.")
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode.")
    parser.add_argument("--user-data-dir", help="Chrome user data directory for session reuse.")
    parser.add_argument("--profile-dir", help="Chrome profile directory name.")
    parser.add_argument("--output-csv", default="naukri_recommended_jobs_sections.csv", help="Output CSV path.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main() -> None:
    script_env = Path(__file__).with_name(DEFAULT_ENV_FILE)
    load_env_file(script_env)
    if not script_env.exists():
        load_env_file(Path(DEFAULT_ENV_FILE))

    args = build_arg_parser().parse_args()
    if not args.headless:
        args.headless = parse_bool(os.getenv("HEADLESS") or os.getenv("headless"), default=False)
    setup_logging(args.verbose)

    driver = None
    try:
        driver = create_driver(args.headless, args.user_data_dir, args.profile_dir)
        logging.info("Chrome driver created.")
        login_if_needed(driver)
        wait_for_document_ready(driver, timeout=30)

        page_text = (driver.page_source or "").lower()
        if "access denied" in page_text:
            raise RuntimeError("Access denied on Recommended Jobs page.")

        tabs = wait_for_tabs(driver, timeout=15)
        if not tabs:
            raise RuntimeError("Recommended Jobs tabs not found.")

        ordered_tab_ids = [tid for tid in TAB_ID_TO_SECTION.keys() if tid in tabs] + [
            tid for tid in tabs.keys() if tid not in TAB_ID_TO_SECTION
        ]

        all_cards: List[RecommendedJobCard] = []
        for tab_id in ordered_tab_ids:
            try:
                cards = click_tab_and_collect(driver, tab_id, tabs[tab_id])
            except Exception as exc:
                if args.verbose:
                    logging.exception("Failed while extracting section tab '%s'.", tab_id)
                else:
                    logging.error("Failed while extracting section tab '%s': %s", tab_id, exc)
                cards = []
            all_cards.extend(cards)

        out_path = Path(args.output_csv)
        fieldnames = [
            "section",
            "job_id",
            "title",
            "company",
            "location",
            "experience",
            "salary",
            "posted",
            "tags",
            "description",
            "job_url",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for card in all_cards:
                row = asdict(card)
                row["tags"] = "; ".join(card.tags or [])
                writer.writerow({k: row.get(k, "") for k in fieldnames})
        logging.info("Wrote %s cards to %s", len(all_cards), out_path)

    except Exception as exc:
        if args.verbose:
            logging.exception("Recommended Jobs extraction failed.")
        else:
            logging.error("Recommended Jobs extraction failed: %s", exc)
        raise SystemExit(1)
    finally:
        if driver:
            try:
                driver.quit()
            except WebDriverException:
                pass


if __name__ == "__main__":
    main()
