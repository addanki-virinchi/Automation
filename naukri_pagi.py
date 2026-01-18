import csv
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import List

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


EMAIL = "major@gmail.com"
PASSWORD = "majili"
RECOMMENDED_URL = "https://www.naukri.com/python-development-jobs?k=python%20development&nignbevent_src=jobsearchDeskGNB"
OUTPUT_CSV = "naukri_jobs_pagi.csv"
MAX_PAGES = 10
MIN_DELAY = 1.5
MAX_DELAY = 3.0

LOG_LEVEL = logging.INFO
LOG_SAMPLE_ROWS = 3
DEBUG_DUMP_HTML = os.getenv("NAUKRI_DEBUG_HTML") == "1"
DEBUG_DUMP_CARD_PATH = "naukri_debug_card.html"
DEBUG_DUMP_PAGE_PATH = "naukri_debug_page.html"
DEBUG_DUMP_DETAIL_PATH = "naukri_debug_detail.html"
DEBUG_DUMPED = False
DEBUG_DUMP_DETAIL_DONE = False

TITLE_SELECTORS = [
    "p.title",
    "p.title[title]",
    "a.title",
    "a.jobTitle",
    "a[title][href*='job-listings']",
    "a[title][href*='job-']",
    "a[href*='job-listings']",
    "a[href*='job-']",
    "span.title",
    "span[class*='jobTitle']",
]

TITLE_LINK_SELECTORS = [
    "a.title",
    "a.jobTitle",
    "a[title][href*='job-listings']",
    "a[title][href*='job-']",
    "a[href*='job-listings']",
    "a[href*='job-']",
]

COMPANY_SELECTORS = [
    "a.subTitle",
    "a.companyName",
    "a.comp-name",
    "a.company-name",
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
    "span[title*='Location']",
    "span[title*='location']",
    "span[class*='loc']",
]

EXPERIENCE_SELECTORS = [
    "li.placeHolderLi.experience span",
    "li.experience span",
    "span.exp",
    "span.experience",
    "span.expwdth",
    "span[title*='Experience']",
    "span[title*='experience']",
    "span[class*='exp']",
]

JOB_CARD_SELECTORS = [
    "div.jobTuple",
    "article.jobTuple",
    "div.rec-job-card",
    "div.recCard",
    "div.jobTupleHeader",
    "div.srp-jobtuple-wrapper",
    "div[class*='jobTuple']",
    "article[class*='jobTuple']",
    "div[class*='job-card']",
    "div[class*='jobCard']",
    "div[class*='jobTupleHeader']",
]

DETAIL_DESCRIPTION_SELECTORS = [
    "div.job-desc",
    "section.job-desc",
    "div.description",
    "div#job-description",
    "section#jobDescription",
    "section[class*='job-desc']",
    "section[class*='jobDescription']",
    "div[class*='jobDescription']",
    "div[class*='job-desc']",
    "div.job-description",
    "div.job-description span",
]

DETAIL_SKILL_SELECTORS = [
    "a.skill",
    "span.skill",
    "div.key-skill",
    "div.job-keyskills a",
    "a.chip",
    "div[class*='skill'] a",
    "span[class*='skill']",
    "ul.tags li",
    "ul[class*='skill'] li",
    "section[class*='skill'] li",
    "div[class*='skill'] li",
]

DETAIL_SALARY_SELECTORS = [
    "span.salary",
    "div.salary",
    "span.sal",
    "span[title*='Salary']",
    "span[class*='salary']",
    "span[class*='compensation']",
]

DETAIL_COMPANY_SELECTORS = [
    "div.company-details",
    "section.company",
    "div.about-company",
    "section.about-company",
    "div[class*='aboutCompany']",
    "div[class*='companyDetail']",
]

DETAIL_REQUIREMENT_SELECTORS = [
    "div.other-details",
    "section.requirements",
    "div.requirements",
    "section.other-details",
    "div[class*='other-detail']",
    "div[class*='requirement']",
]

DETAIL_READY_SELECTORS = DETAIL_DESCRIPTION_SELECTORS + DETAIL_SKILL_SELECTORS

CSV_FIELDS = [
    "Job Title",
    "Company",
    "Location",
    "Experience",
    "Job ID",
    "Listing URL",
    "Job URL",
    "Salary",
    "Skills",
    "Description",
    "Company Details",
    "Additional Requirements",
]

LOGIN_ERROR_SELECTORS = [
    "div.error",
    "div.err",
    "span.error",
    "p.error",
    "div[class*='error']",
]

LOGIN_ERROR_KEYWORDS = [
    "invalid",
    "incorrect",
    "try again",
    "please enter",
    "captcha",
    "verification",
]

NOT_FOUND_KEYWORDS = [
    "could not be found",
    "page not found",
    "404",
]

DESCRIPTION_KEYWORDS = [
    "job description",
    "responsibilities",
    "requirements",
    "qualification",
    "role",
    "skills",
    "experience",
]

DESCRIPTION_EXCLUDE = [
    "reviews",
    "salary insights",
    "benefits",
    "similar jobs",
    "follow",
    "services you might be interested",
]


@dataclass
class JobSummary:
    title: str
    company: str
    location: str
    experience: str
    job_id: str
    listing_url: str
    constructed_url: str


@dataclass
class JobDetail:
    salary: str
    skills: str
    description: str
    company_details: str
    additional_requirements: str


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def delay():
    time.sleep(MIN_DELAY + (MAX_DELAY - MIN_DELAY) * 0.5)


def setup_logging():
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def trim_text(value: str, max_len: int = 160) -> str:
    if not value:
        return ""
    compact = re.sub(r"\s+", " ", value.strip())
    if len(compact) > max_len:
        return compact[: max_len - 3] + "..."
    return compact


def wait_for_document_ready(driver, timeout: int = 15):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        pass


def safe_text(element) -> str:
    try:
        text = element.text or ""
    except StaleElementReferenceException:
        return ""
    return text.strip()


def safe_get_attribute(element, attr: str) -> str:
    try:
        return element.get_attribute(attr) or ""
    except StaleElementReferenceException:
        return ""


def log_job_card_counts(driver):
    counts = {}
    for sel in JOB_CARD_SELECTORS:
        try:
            counts[sel] = len(driver.find_elements(By.CSS_SELECTOR, sel))
        except WebDriverException as exc:
            counts[sel] = f"err:{exc.__class__.__name__}"
    logging.info("Job card selector counts: %s", counts)


def is_probable_job_card(card) -> bool:
    if safe_get_attribute(card, "data-job-id"):
        return True
    try:
        title_links = card.find_elements(By.CSS_SELECTOR, ", ".join(TITLE_LINK_SELECTORS))
    except StaleElementReferenceException:
        return False
    return bool(title_links)


def find_job_cards(driver):
    cards = []
    seen = set()
    for sel in JOB_CARD_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
        except WebDriverException:
            continue
        for element in elements:
            element_id = element.id
            if element_id in seen:
                continue
            if not is_probable_job_card(element):
                continue
            seen.add(element_id)
            cards.append(element)
    return cards


def dump_debug_html(driver, card=None):
    global DEBUG_DUMPED
    if not DEBUG_DUMP_HTML or DEBUG_DUMPED:
        return
    try:
        if card:
            card_html = card.get_attribute("outerHTML") or ""
            if card_html:
                with open(DEBUG_DUMP_CARD_PATH, "w", encoding="utf-8") as f:
                    f.write(card_html)
        page_html = driver.page_source or ""
        if page_html:
            with open(DEBUG_DUMP_PAGE_PATH, "w", encoding="utf-8") as f:
                f.write(page_html)
        DEBUG_DUMPED = True
        logging.info("Debug HTML written to %s and %s.", DEBUG_DUMP_CARD_PATH, DEBUG_DUMP_PAGE_PATH)
    except (OSError, WebDriverException):
        logging.exception("Failed to write debug HTML.")


def dump_detail_html(driver):
    global DEBUG_DUMP_DETAIL_DONE
    if not DEBUG_DUMP_HTML or DEBUG_DUMP_DETAIL_DONE:
        return
    try:
        page_html = driver.page_source or ""
        if page_html:
            with open(DEBUG_DUMP_DETAIL_PATH, "w", encoding="utf-8") as f:
                f.write(page_html)
        DEBUG_DUMP_DETAIL_DONE = True
        logging.info("Detail debug HTML written to %s.", DEBUG_DUMP_DETAIL_PATH)
    except (OSError, WebDriverException):
        logging.exception("Failed to write detail debug HTML.")


def normalize_job_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://www.naukri.com{url}"
    return url


def is_valid_job_url(url: str) -> bool:
    if not url:
        return False
    lower = url.lower()
    if lower in ("#", "about:blank"):
        return False
    if lower.startswith("javascript"):
        return False
    return True


def extract_job_url_from_element(element) -> str:
    for attr in ["data-jdurl", "data-jd-url", "data-job-url", "data-joburl", "data-href", "data-url"]:
        candidate = normalize_job_url(safe_get_attribute(element, attr))
        if is_valid_job_url(candidate):
            return candidate
    return ""


def find_attribute_in_card(card, attr_names: List[str]) -> str:
    for attr in attr_names:
        value = safe_get_attribute(card, attr)
        if value:
            return value
        try:
            elements = card.find_elements(By.CSS_SELECTOR, f"[{attr}]")
        except StaleElementReferenceException:
            return ""
        for element in elements:
            value = safe_get_attribute(element, attr)
            if value:
                return value
    return ""


def detect_login_error(driver) -> str:
    for sel in LOGIN_ERROR_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
        except WebDriverException:
            continue
        for element in elements:
            text = safe_text(element)
            if not text:
                continue
            text_lower = text.lower()
            if any(keyword in text_lower for keyword in LOGIN_ERROR_KEYWORDS):
                return text
    return ""


def log_sample_data(index: int, summary: JobSummary, details: JobDetail):
    logging.info(
        "Sample %s summary | title=%s | company=%s | location=%s | experience=%s | job_id=%s",
        index,
        trim_text(summary.title, 80),
        trim_text(summary.company, 60),
        trim_text(summary.location, 60),
        trim_text(summary.experience, 40),
        summary.job_id,
    )
    logging.info(
        "Sample %s details | salary=%s | skills=%s | description=%s",
        index,
        trim_text(details.salary, 60),
        trim_text(details.skills, 80),
        trim_text(details.description, 160),
    )


def find_first(parent, selectors: List[str]):
    for sel in selectors:
        try:
            elements = parent.find_elements(By.CSS_SELECTOR, sel)
        except StaleElementReferenceException:
            return None
        if elements:
            return elements[0]
    return None


def first_text(element, selectors: List[str]) -> str:
    for sel in selectors:
        try:
            handles = element.find_elements(By.CSS_SELECTOR, sel)
        except StaleElementReferenceException:
            return ""
        for handle in handles:
            text = safe_text(handle)
            if text:
                return text
    return ""


def first_text_page(driver, selectors: List[str]) -> str:
    return first_text(driver, selectors)


def all_texts_page(driver, selectors: List[str]) -> str:
    for sel in selectors:
        try:
            handles = driver.find_elements(By.CSS_SELECTOR, sel)
        except StaleElementReferenceException:
            return ""
        parts = []
        for handle in handles:
            text = safe_text(handle)
            if text:
                parts.append(text)
        if parts:
            return ", ".join(parts)
    return ""


def extract_job_id_from_href(href: str) -> str:
    if not href:
        return ""
    match = re.search(r"(?:jobid|job_id)=(\d+)", href, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"-(\d{6,})(?:\\?|$)", href)
    if match:
        return match.group(1)
    return ""


def is_not_found_page(driver) -> bool:
    try:
        snippet = driver.execute_script(
            "return document.body && document.body.innerText ? document.body.innerText.slice(0, 1000) : ''"
        )
    except WebDriverException:
        return False
    snippet_lower = (snippet or "").lower()
    return any(keyword in snippet_lower for keyword in NOT_FOUND_KEYWORDS)


def wait_for_any_selector(driver, selectors: List[str], timeout: int = 5) -> bool:
    def _has_any(drv):
        try:
            return any(drv.find_elements(By.CSS_SELECTOR, sel) for sel in selectors)
        except WebDriverException:
            return False

    try:
        WebDriverWait(driver, timeout).until(_has_any)
        return True
    except TimeoutException:
        return False


def wait_for_job_cards(driver, timeout: int = 10) -> bool:
    try:
        WebDriverWait(driver, timeout).until(lambda d: len(find_job_cards(d)) > 0)
        return True
    except TimeoutException:
        return False


def wait_for_detail_content(driver, timeout: int = 10) -> bool:
    return wait_for_any_selector(driver, DETAIL_READY_SELECTORS, timeout=timeout)


def login_if_needed(driver):
    if not driver.current_url or not driver.current_url.startswith(RECOMMENDED_URL):
        logging.info("Navigating to recommended jobs page.")
        try:
            driver.get(RECOMMENDED_URL)
        except TimeoutException:
            pass
        wait_for_document_ready(driver, timeout=30)
        time.sleep(1)
        logging.info("Current URL after navigation: %s", driver.current_url)

    login_selectors = [
        "input#usernameField",
        "input[name='email']",
        "input[type='text'][placeholder*='Email']",
    ]
    if any(driver.find_elements(By.CSS_SELECTOR, sel) for sel in login_selectors):
        logging.info("Login form detected. Attempting login.")
        email_field = find_first(
            driver,
            ["input#usernameField", "input[name='email']", "input[type='text']"],
        )
        password_field = find_first(
            driver,
            ["input#passwordField", "input[type='password']"],
        )
        if not email_field or not password_field:
            raise RuntimeError("Login fields not found on the page.")

        email_field.clear()
        email_field.send_keys(EMAIL)
        password_field.clear()
        password_field.send_keys(PASSWORD)

        submit_btn = find_first(driver, ["button[type='submit']"])
        if not submit_btn:
            login_buttons = driver.find_elements(
                By.XPATH,
                "//button[contains(translate(normalize-space(.), 'LOGIN', 'login'), 'login')]",
            )
            submit_btn = login_buttons[0] if login_buttons else None
        if not submit_btn:
            raise RuntimeError("Login button not found.")

        submit_btn.click()

        wait_for_document_ready(driver, timeout=30)
        time.sleep(2)

        try:
            WebDriverWait(driver, 10).until(
                lambda d: RECOMMENDED_URL in d.current_url
                or not any(d.find_elements(By.CSS_SELECTOR, sel) for sel in login_selectors)
            )
        except TimeoutException:
            pass

        if any(driver.find_elements(By.CSS_SELECTOR, sel) for sel in login_selectors):
            logging.warning("Login form still present after submit.")

        login_error = detect_login_error(driver)
        if login_error:
            logging.warning("Login error message detected: %s", trim_text(login_error, 120))

        if not wait_for_job_cards(driver, timeout=10):
            logging.warning("Job cards not detected after login.")
            log_job_card_counts(driver)
            page_text = driver.page_source.lower()
            if "captcha" in page_text or "verify" in page_text:
                logging.warning("Page may be blocked by captcha or verification step.")
            logging.info("Retrying recommended jobs page after login.")
            try:
                driver.get(RECOMMENDED_URL)
            except TimeoutException:
                pass
            wait_for_document_ready(driver, timeout=20)
            if wait_for_job_cards(driver, timeout=10):
                logging.info("Job cards detected after retrying recommended jobs.")
            else:
                logging.warning("Job cards still not detected after retry.")
        else:
            logging.info("Login completed and job cards are visible.")
    else:
        logging.info("Login form not detected. Assuming already logged in.")


def collect_job_summaries(driver) -> List[JobSummary]:
    cards = find_job_cards(driver)
    logging.info("Job cards found: %s", len(cards))
    if not cards:
        log_job_card_counts(driver)
    else:
        dump_debug_html(driver, cards[0])
    summaries = []
    sample_index = 0
    for card in cards:
        title = first_text(card, TITLE_SELECTORS)
        company = first_text(card, COMPANY_SELECTORS)
        location = first_text(card, LOCATION_SELECTORS)
        experience = first_text(card, EXPERIENCE_SELECTORS)

        title_link = find_first(card, TITLE_LINK_SELECTORS)
        href = ""
        if title_link:
            href = normalize_job_url(safe_get_attribute(title_link, "href"))
            if not is_valid_job_url(href):
                href = extract_job_url_from_element(title_link)
        if not is_valid_job_url(href):
            for link in card.find_elements(By.TAG_NAME, "a"):
                candidate = normalize_job_url(safe_get_attribute(link, "href"))
                if not is_valid_job_url(candidate):
                    candidate = extract_job_url_from_element(link)
                if candidate and "review" not in candidate.lower():
                    href = candidate
                    break
        if not is_valid_job_url(href):
            href = extract_job_url_from_element(card)
        if href and "review" in href.lower():
            href = ""

        job_id = find_attribute_in_card(card, ["data-job-id", "data-jobid"]) or extract_job_id_from_href(href)

        if not title and title_link:
            title = safe_text(title_link)

        if not (title or href or job_id):
            continue

        constructed_url = ""
        if title and company and location and experience and job_id:
            constructed_url = (
                f"https://www.naukri.com/job-listings-"
                f"{slugify(title)}-{slugify(company)}-{slugify(location)}-"
                f"{slugify(experience)}-{job_id}"
            )

        summaries.append(
            JobSummary(
                title=title,
                company=company,
                location=location,
                experience=experience,
                job_id=job_id,
                listing_url=href or "",
                constructed_url=constructed_url,
            )
        )
        if sample_index < LOG_SAMPLE_ROWS:
            sample_index += 1
            logging.info(
                "Summary sample %s | title=%s | company=%s | location=%s | exp=%s | href=%s | job_id=%s",
                sample_index,
                trim_text(title, 80),
                trim_text(company, 60),
                trim_text(location, 60),
                trim_text(experience, 40),
                trim_text(href, 120),
                job_id,
            )

    logging.info("Job summaries extracted: %s", len(summaries))
    return summaries


def extract_job_details(driver) -> JobDetail:
    description = first_text_page(driver, DETAIL_DESCRIPTION_SELECTORS)
    if not description:
        description = extract_description_fallback(driver)
    skills = all_texts_page(driver, DETAIL_SKILL_SELECTORS)
    salary = first_text_page(driver, DETAIL_SALARY_SELECTORS)
    company_details = first_text_page(driver, DETAIL_COMPANY_SELECTORS)
    additional_requirements = first_text_page(driver, DETAIL_REQUIREMENT_SELECTORS)
    return JobDetail(
        salary=salary,
        skills=skills,
        description=description,
        company_details=company_details,
        additional_requirements=additional_requirements,
    )


def extract_description_fallback(driver) -> str:
    selectors = [
        "section[class*='desc']",
        "section[class*='detail']",
        "section[class*='job']",
        "div[class*='desc']",
        "div[class*='detail']",
        "div[class*='job']",
        "section",
    ]
    best_text = ""
    best_score = 0
    seen = set()
    for sel in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
        except WebDriverException:
            continue
        for element in elements:
            element_id = element.id
            if element_id in seen:
                continue
            seen.add(element_id)
            text = safe_text(element)
            if len(text) < 120:
                continue
            lower = text.lower()
            if any(exclude in lower for exclude in DESCRIPTION_EXCLUDE):
                continue
            score = sum(1 for keyword in DESCRIPTION_KEYWORDS if keyword in lower)
            if score == 0:
                continue
            if score > best_score or (score == best_score and len(text) > len(best_text)):
                best_score = score
                best_text = text
    return best_text


def ensure_listings_loaded(driver, listings_url: str):
    if wait_for_job_cards(driver, timeout=5):
        return
    logging.info("Job cards not visible after navigation. Reloading listings.")
    try:
        driver.get(listings_url)
    except TimeoutException:
        pass
    wait_for_document_ready(driver, timeout=15)
    if not wait_for_job_cards(driver, timeout=10):
        logging.warning("Job cards still not detected after reload.")
        log_job_card_counts(driver)


def go_to_next_page(driver) -> bool:
    next_btn = find_first(
        driver,
        ["a[title='Next']", "a[aria-label='Next']", "a.pagination-next"],
    )
    if not next_btn:
        logging.info("Next page button not found.")
        return False
    btn_class = safe_get_attribute(next_btn, "class").lower()
    aria_disabled = safe_get_attribute(next_btn, "aria-disabled").lower()
    if "disabled" in btn_class or aria_disabled == "true":
        logging.info("Next page button is disabled.")
        return False

    try:
        first_card = None
        cards = find_job_cards(driver)
        if cards:
            first_card = cards[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
        except WebDriverException:
            pass
        logging.info("Navigating to next page.")
        next_btn.click()
        try:
            if first_card:
                WebDriverWait(driver, 15).until(EC.staleness_of(first_card))
            else:
                wait_for_document_ready(driver, timeout=15)
        except TimeoutException:
            pass
        time.sleep(1)
        logging.info("Next page loaded: %s", driver.current_url)
        return True
    except WebDriverException:
        logging.exception("Failed while trying to navigate to next page.")
        return False


def create_driver():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=en-US")
    driver = uc.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


def main():
    setup_logging()
    logging.info("Starting Naukri scraper.")
    all_rows = []
    driver = None
    try:
        driver = create_driver()
        logging.info("Chrome driver created.")
        login_if_needed(driver)
        wait_for_document_ready(driver, timeout=15)
        time.sleep(1.5)
        if not wait_for_job_cards(driver, timeout=10):
            logging.warning("No job cards detected after initial load.")
            log_job_card_counts(driver)
        logging.info("Current URL: %s", driver.current_url)
        logging.info("Page title: %s", driver.title)

        page_count = 0
        sample_count = 0
        while page_count < MAX_PAGES:
            page_count += 1
            delay()
            logging.info("Processing page %s at %s", page_count, driver.current_url)
            summaries = collect_job_summaries(driver)
            if not summaries:
                logging.warning("No job summaries extracted on page %s.", page_count)
                page_text = driver.page_source.lower()
                if "captcha" in page_text or "verify" in page_text:
                    logging.warning("Page content suggests a captcha/verification gate.")
                break

            listings_url = driver.current_url
            for summary in summaries:
                job_url = summary.constructed_url or summary.listing_url
                if not job_url and summary.job_id:
                    job_url = f"https://www.naukri.com/job-listings?jobId={summary.job_id}"
                if job_url and "review" in job_url.lower():
                    job_url = ""
                if not job_url:
                    continue

                try:
                    if sample_count < LOG_SAMPLE_ROWS:
                        logging.info("Opening job detail: %s", job_url)
                    try:
                        driver.get(job_url)
                    except TimeoutException:
                        pass
                    wait_for_document_ready(driver, timeout=10)
                    if is_not_found_page(driver) and summary.job_id:
                        fallback_url = f"https://www.naukri.com/job-listings?jobId={summary.job_id}"
                        if fallback_url != job_url:
                            logging.warning("Detail page not found. Retrying with %s", fallback_url)
                            job_url = fallback_url
                            try:
                                driver.get(fallback_url)
                            except TimeoutException:
                                pass
                            wait_for_document_ready(driver, timeout=10)
                    if not wait_for_detail_content(driver, timeout=15):
                        logging.warning("Detail content not detected for %s", job_url)
                    delay()
                    details = extract_job_details(driver)
                    if not details.description:
                        dump_detail_html(driver)
                except TimeoutException:
                    logging.warning("Timeout while loading job detail: %s", job_url)
                    details = JobDetail("", "", "", "", "")
                except Exception:
                    logging.exception("Unexpected error while extracting job detail: %s", job_url)
                    details = JobDetail("", "", "", "", "")

                row = {
                    "Job Title": summary.title,
                    "Company": summary.company,
                    "Location": summary.location,
                    "Experience": summary.experience,
                    "Job ID": summary.job_id,
                    "Listing URL": summary.listing_url,
                    "Job URL": job_url,
                    "Salary": details.salary,
                    "Skills": details.skills,
                    "Description": details.description,
                    "Company Details": details.company_details,
                    "Additional Requirements": details.additional_requirements,
                }
                all_rows.append(row)
                if sample_count < LOG_SAMPLE_ROWS:
                    sample_count += 1
                    log_sample_data(sample_count, summary, details)

                try:
                    driver.back()
                except WebDriverException:
                    pass
                wait_for_document_ready(driver, timeout=10)
                ensure_listings_loaded(driver, listings_url)

            if not go_to_next_page(driver):
                break
            remember_url = driver.current_url
            if remember_url != listings_url:
                listings_url = remember_url

    finally:
        if driver:
            driver.quit()
            logging.info("Chrome driver closed.")

    try:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for row in all_rows:
                writer.writerow(row)
        logging.info("CSV written to %s with %s rows.", OUTPUT_CSV, len(all_rows))
    except OSError:
        logging.exception("Failed to write CSV to %s.", OUTPUT_CSV)
    if not all_rows:
        logging.warning("No job rows were extracted. CSV contains only headers.")


if __name__ == "__main__":
    main()
