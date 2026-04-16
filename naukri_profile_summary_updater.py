import argparse
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None


PROFILE_URL = "https://www.naukri.com/mnjuser/profile"
LOGIN_URL = "https://www.naukri.com/nlogin/login"
DEFAULT_PROFILE_HTML = "profile.html"
DEFAULT_SUMMARY_FILE = "profile_summaries.txt"
LEGACY_SUMMARY_FILE = "Profile_Summeries.txt"
DEFAULT_TASK_NAME = "NaukriProfileEditor"
IST_TIMEZONE = "Asia/Kolkata"
DEFAULT_SCHEDULE_TIME = "13:58"
DEFAULT_ENV_FILE = ".env"

NBSP = "\u00A0"
LOG_PATH = Path(__file__).with_suffix(".log")
DEBUG_DIR = Path(__file__).with_name("debug_profile_editor")

LOGIN_FIELD_SELECTORS = [
    "input#usernameField",
    "input[name='email']",
    "input[type='text'][placeholder*='Email']",
]

PASSWORD_FIELD_SELECTORS = [
    "input#passwordField",
    "input[type='password']",
]

PROFILE_SUMMARY_CARD_SELECTORS = [
    "div.profileSummary",
    "section.profileSummary",
]

PROFILE_SUMMARY_CONTAINER_SELECTORS = [
    "div#lazyProfileSummary",
    "div[data-plugin='lazyload']#lazyProfileSummary",
]

PROFILE_SUMMARY_TEXT_SELECTORS = [
    ".profileSummary .prefill",
    ".profileSummary .prefill div",
    ".profileSummary .widgetCont",
]

PROFILE_SUMMARY_EDIT_SELECTORS = [
    ".profileSummary .widgetHead .edit",
    ".profileSummary .widgetHead .icon.edit",
    ".profileSummary span.edit",
    ".profileSummary em.icon.edit",
    ".profileSummary i.icon.edit",
    ".profileSummary .edit.icon",
    ".profileSummary .editOneTheme",
    ".profileSummary [aria-label*='Edit']",
    ".profileSummary [title*='Edit']",
]

PROFILE_SUMMARY_DRAWER_SELECTORS = [
    ".profileSummaryEdit",
    ".profileEditDrawer.profileSummaryEdit",
    ".profileEditDrawer",
]

PROFILE_SUMMARY_EDIT_ICON_SELECTORS = [
    "#lazyProfileSummary .profileSummary .widgetHead .edit.icon",
    ".profileSummary .widgetHead .edit.icon",
    ".profileSummary .widgetHead .edit",
    ".profileSummary .widgetHead .icon.edit",
]

SUMMARY_INPUT_SELECTORS = [
    "textarea[name='profileSummary']",
    "textarea#profileSummary",
    "textarea[name='profile_summary']",
    "textarea[placeholder*='Profile']",
    "textarea[placeholder*='summary']",
    "div[contenteditable='true']",
    "div[role='textbox']",
]

SAVE_BUTTON_XPATH = (
    "//button[normalize-space()='Save' or normalize-space()='SAVE' or "
    "contains(translate(normalize-space(.), 'SAVE', 'save'), 'save') or "
    "contains(translate(normalize-space(.), 'UPDATE', 'update'), 'update')]"
)

LOGIN_ERROR_KEYWORDS = [
    "invalid",
    "incorrect",
    "try again",
    "captcha",
    "verification",
    "otp",
]


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


class ProfileSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.profile_depth = 0
        self.prefill_depth = 0
        self._stack = []
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        classes = ""
        for key, value in attrs:
            if key == "class":
                classes = value or ""
                break
        class_list = classes.split()
        is_profile = "profileSummary" in class_list
        if is_profile:
            self.profile_depth += 1
        effective_prefill = self.profile_depth > 0 and "prefill" in class_list
        if effective_prefill:
            self.prefill_depth += 1
        self._stack.append((is_profile, effective_prefill))

    def handle_endtag(self, tag):
        if not self._stack:
            return
        is_profile, effective_prefill = self._stack.pop()
        if effective_prefill:
            self.prefill_depth = max(0, self.prefill_depth - 1)
        if is_profile:
            self.profile_depth = max(0, self.profile_depth - 1)

    def handle_data(self, data):
        if self.prefill_depth > 0:
            text = (data or "").strip()
            if text:
                self._chunks.append(text)

    def result(self) -> str:
        return " ".join(self._chunks).strip()


def extract_profile_summary_from_html(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        html = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    parser = ProfileSummaryParser()
    try:
        parser.feed(html)
    except Exception:
        return ""
    return parser.result()


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    cleaned = value.strip().lower()
    if cleaned in {"1", "true", "yes", "y", "on"}:
        return True
    if cleaned in {"0", "false", "no", "n", "off"}:
        return False
    return default


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    try:
        raw_lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        os.environ[key] = value


def resolve_summary_file(path: Path) -> Path:
    if path.exists():
        return path
    name = path.name.lower()
    if name == LEGACY_SUMMARY_FILE.lower():
        candidate = path.with_name(DEFAULT_SUMMARY_FILE)
        if candidate.exists():
            return candidate
    if name == DEFAULT_SUMMARY_FILE.lower():
        candidate = path.with_name(LEGACY_SUMMARY_FILE)
        if candidate.exists():
            return candidate
    return path


def load_summary_lines(path: Path) -> Dict[int, str]:
    path = resolve_summary_file(path)
    if not path.exists():
        raise RuntimeError(f"Summary file not found: {path}")
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        raise RuntimeError(f"Unable to read summary file: {path}") from exc

    day_map: Dict[int, str] = {}
    fallback: List[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^Day\s*-\s*(\d+)\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if match:
            day_index = int(match.group(1))
            text = match.group(2).strip()
            if 1 <= day_index <= 7 and text:
                day_map[day_index] = text
            elif text:
                fallback.append(text)
        else:
            fallback.append(line)

    if not day_map and not fallback:
        raise RuntimeError("Summary file has no usable lines.")

    # Fill missing days from fallback lines (ordered).
    if fallback:
        for idx, text in enumerate(fallback, start=1):
            if idx > 7:
                break
            if idx not in day_map and text:
                day_map[idx] = text

    return day_map


def current_day_index_ist() -> int:
    if ZoneInfo is None:
        return datetime.now().isoweekday()
    ist = ZoneInfo(IST_TIMEZONE)
    return datetime.now(ist).isoweekday()


def select_daily_summary(summary_file: Path) -> Tuple[int, str]:
    day_map = load_summary_lines(summary_file)
    day_index = current_day_index_ist()
    summary = day_map.get(day_index)
    if not summary:
        raise RuntimeError(f"No summary found for Day-{day_index} in {summary_file}.")
    return day_index, summary


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
    driver.set_page_load_timeout(30)
    return driver


def wait_for_document_ready(driver, timeout: int = 20):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        pass


def find_first(parent, selectors):
    for selector in selectors:
        try:
            elements = parent.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        if elements:
            return elements[0]
    return None


def any_visible(parent, selectors) -> bool:
    for selector in selectors:
        try:
            for element in parent.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    return True
        except WebDriverException:
            continue
    return False


def has_profile_summary_widget(driver) -> bool:
    selectors = PROFILE_SUMMARY_CARD_SELECTORS + PROFILE_SUMMARY_CONTAINER_SELECTORS
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        if elements:
            return True
    try:
        elements = driver.find_elements(
            By.XPATH,
            "//span[contains(@class,'widgetTitle') and "
            "contains(translate(., 'PROFILE SUMMARY', 'profile summary'), 'profile summary')]",
        )
        if elements:
            return True
    except WebDriverException:
        pass
    return False


def wait_for_profile_summary_ready(driver, timeout: int = 15) -> bool:
    def _ready(drv):
        for selector in PROFILE_SUMMARY_EDIT_ICON_SELECTORS:
            try:
                if drv.find_elements(By.CSS_SELECTOR, selector):
                    return True
            except WebDriverException:
                continue
        try:
            return bool(
                drv.execute_script(
                    "var el = document.getElementById('lazyProfileSummary');"
                    "if (!el) return false;"
                    "var loaded = el.getAttribute('data-loaded');"
                    "if (loaded === 'true') return true;"
                    "return !!el.querySelector('.profileSummary .widgetHead');"
                )
            )
        except WebDriverException:
            return False

    try:
        WebDriverWait(driver, timeout).until(_ready)
        return True
    except TimeoutException:
        return False


def scroll_to_profile_summary(driver) -> bool:
    try:
        return bool(
            driver.execute_script(
                "var el = document.getElementById('lazyProfileSummary') || "
                "document.querySelector('.profileSummary');"
                "if (el) { el.scrollIntoView({block: 'center'}); return true; }"
                "return false;"
            )
        )
    except WebDriverException:
        return False


def scroll_until_profile_summary(driver, max_attempts: int = 6) -> bool:
    for attempt in range(max_attempts):
        if has_profile_summary_widget(driver):
            return True
        try:
            height = driver.execute_script("return document.body.scrollHeight || 0;") or 0
            target = int(height * (attempt + 1) / max_attempts) if height else 0
            driver.execute_script("window.scrollTo(0, arguments[0]);", target)
        except WebDriverException:
            pass
        time.sleep(1.2)
    return has_profile_summary_widget(driver)


def click_quick_link_profile_summary(driver) -> bool:
    xpath = (
        "//li[contains(@class,'collection-item')]"
        "//span[contains(translate(., 'PROFILE SUMMARY', 'profile summary'), 'profile summary')]"
    )
    try:
        element = driver.find_element(By.XPATH, xpath)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        element.click()
        time.sleep(1.0)
        return True
    except WebDriverException:
        return False


def detect_login_form(driver) -> bool:
    return any(driver.find_elements(By.CSS_SELECTOR, sel) for sel in LOGIN_FIELD_SELECTORS)


def page_contains_login_issue(driver) -> bool:
    try:
        snippet = driver.execute_script(
            "return document.body && document.body.innerText ? document.body.innerText.slice(0, 2000) : ''"
        )
    except WebDriverException:
        return False
    text = (snippet or "").lower()
    return any(keyword in text for keyword in LOGIN_ERROR_KEYWORDS)


def login_if_needed(driver, email: str, password: str) -> None:
    if not detect_login_form(driver):
        return
    logging.info("Login form detected. Attempting login.")
    email_field = find_first(driver, LOGIN_FIELD_SELECTORS)
    password_field = find_first(driver, PASSWORD_FIELD_SELECTORS)
    if not email_field or not password_field:
        raise RuntimeError("Login fields not found on the page.")
    email_field.clear()
    email_field.send_keys(email)
    password_field.clear()
    password_field.send_keys(password)
    submit_btn = find_first(driver, ["button[type='submit']", "button.loginButton", "button#loginBtn"])
    if not submit_btn:
        buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Login') or contains(., 'LOGIN')]")
        submit_btn = buttons[0] if buttons else None
    if not submit_btn:
        raise RuntimeError("Login button not found.")
    submit_btn.click()
    wait_for_document_ready(driver, timeout=20)
    time.sleep(2)
    if detect_login_form(driver) or page_contains_login_issue(driver):
        raise RuntimeError("Login appears unsuccessful. Please check credentials or captcha/OTP.")
    logging.info("Login completed.")


def navigate_to_profile(driver, email: str, password: str) -> None:
    last_error = None
    for attempt in range(2):
        driver.get(PROFILE_URL)
        wait_for_document_ready(driver, timeout=30)
        time.sleep(1.5)
        try:
            if detect_login_form(driver) or "login" in (driver.current_url or "").lower():
                logging.info("Redirected to login page.")
                if not detect_login_form(driver):
                    driver.get(LOGIN_URL)
                    wait_for_document_ready(driver, timeout=20)
                login_if_needed(driver, email, password)
                driver.get(PROFILE_URL)
                wait_for_document_ready(driver, timeout=30)
                time.sleep(1.5)
        except RuntimeError as exc:
            last_error = exc
            logging.warning("Login attempt failed (%s).", exc)
            if attempt == 0:
                try:
                    driver.delete_all_cookies()
                except WebDriverException:
                    pass
                continue
            raise

        if not has_profile_summary_widget(driver):
            scroll_to_profile_summary(driver)
            time.sleep(0.8)
        if not has_profile_summary_widget(driver):
            click_quick_link_profile_summary(driver)
            time.sleep(0.8)
        if scroll_until_profile_summary(driver, max_attempts=6):
            logging.info("Profile page loaded.")
            return
        last_error = RuntimeError("Profile summary card not visible after navigation.")
        if attempt == 0:
            continue
        if page_contains_login_issue(driver):
            raise RuntimeError("Profile page blocked by login/OTP/captcha verification.")
        raise last_error

    if last_error:
        raise last_error


def find_profile_summary_card(driver):
    card = find_first(driver, PROFILE_SUMMARY_CARD_SELECTORS + PROFILE_SUMMARY_CONTAINER_SELECTORS)
    if card:
        if (card.get_attribute("id") or "") == "lazyProfileSummary":
            inner = find_first(card, PROFILE_SUMMARY_CARD_SELECTORS)
            return inner or card
        return card
    try:
        title_el = driver.find_element(
            By.XPATH,
            "//span[contains(@class,'widgetTitle') and "
            "contains(translate(., 'PROFILE SUMMARY', 'profile summary'), 'profile summary')]",
        )
    except WebDriverException:
        return None
    for xpath in [
        "./ancestor::div[contains(@class,'profileSummary')][1]",
        "./ancestor::section[contains(@class,'profileSummary')][1]",
        "./ancestor::div[contains(@class,'card')][1]",
    ]:
        try:
            return title_el.find_element(By.XPATH, xpath)
        except WebDriverException:
            continue
    return None


def get_profile_summary_text(driver) -> str:
    card = find_profile_summary_card(driver)
    if card:
        for selector in PROFILE_SUMMARY_TEXT_SELECTORS:
            try:
                elements = card.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            for element in elements:
                text = (element.text or "").strip()
                if text:
                    return text
    for selector in PROFILE_SUMMARY_TEXT_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            text = (element.text or "").strip()
            if text:
                return text
    return ""


def open_profile_summary_editor(driver) -> None:
    scroll_to_profile_summary(driver)
    time.sleep(0.6)
    wait_for_profile_summary_ready(driver, timeout=10)
    card = find_profile_summary_card(driver)
    if not card:
        scroll_until_profile_summary(driver, max_attempts=6)
        wait_for_profile_summary_ready(driver, timeout=10)
        card = find_profile_summary_card(driver)
    if not card:
        raise RuntimeError("Profile summary card not found.")

    def _drawer_open():
        return any_visible(driver, PROFILE_SUMMARY_DRAWER_SELECTORS)

    def _try_hover(element) -> None:
        if not element:
            return
        try:
            ActionChains(driver).move_to_element(element).perform()
        except WebDriverException:
            pass

    def _safe_click(element) -> bool:
        if not element:
            return False
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        except WebDriverException:
            pass
        try:
            element.click()
            return True
        except WebDriverException:
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except WebDriverException:
                try:
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window}));",
                        element,
                    )
                    return True
                except WebDriverException:
                    return False

    def _locate_edit_icons():
        icons = []
        for selector in PROFILE_SUMMARY_EDIT_ICON_SELECTORS:
            try:
                icons.extend(driver.find_elements(By.CSS_SELECTOR, selector))
            except WebDriverException:
                continue
        try:
            icons.extend(
                card.find_elements(
                    By.XPATH,
                    ".//*[self::span or self::em or self::i or self::a or self::button]"
                    "[contains(@class,'edit') or contains(@class,'icon') or contains(@class,'editOneTheme')]",
                )
            )
        except WebDriverException:
            pass
        unique = []
        seen = set()
        for icon in icons:
            try:
                icon_id = icon.id
            except WebDriverException:
                icon_id = None
            if icon_id and icon_id in seen:
                continue
            if icon_id:
                seen.add(icon_id)
            unique.append(icon)
        return unique

    deadline = time.time() + 25
    while time.time() < deadline:
        _try_hover(card)
        edit_icons = _locate_edit_icons()
        for icon in edit_icons:
            if _safe_click(icon):
                try:
                    WebDriverWait(driver, 6).until(lambda d: _drawer_open())
                    logging.info("Profile summary editor opened (edit icon).")
                    return
                except TimeoutException:
                    pass
        if _drawer_open():
            logging.info("Profile summary editor already open.")
            return
        time.sleep(1.0)
        scroll_to_profile_summary(driver)

    content = find_first(card, PROFILE_SUMMARY_TEXT_SELECTORS)
    if content and _safe_click(content):
        try:
            WebDriverWait(driver, 10).until(lambda d: _drawer_open())
            logging.info("Profile summary editor opened (content click).")
            return
        except TimeoutException:
            pass

    title = None
    try:
        title = card.find_element(
            By.XPATH,
            ".//*[contains(@class,'widgetTitle') and "
            "contains(translate(., 'PROFILE SUMMARY', 'profile summary'), 'profile summary')]",
        )
    except WebDriverException:
        title = None
    if title and _safe_click(title):
        try:
            WebDriverWait(driver, 8).until(lambda d: _drawer_open())
            logging.info("Profile summary editor opened (title click).")
            return
        except TimeoutException:
            pass

    if click_quick_link_profile_summary(driver):
        try:
            WebDriverWait(driver, 10).until(lambda d: _drawer_open())
            logging.info("Profile summary editor opened (quick link).")
            return
        except TimeoutException:
            pass
    if _drawer_open():
        logging.info("Profile summary editor already open after fallbacks.")
        return

    raise RuntimeError("Profile summary editor not opened after multiple click attempts.")


def find_summary_input(driver):
    for selector in SUMMARY_INPUT_SELECTORS:
        for drawer_selector in PROFILE_SUMMARY_DRAWER_SELECTORS:
            try:
                drawers = driver.find_elements(By.CSS_SELECTOR, drawer_selector)
            except WebDriverException:
                drawers = []
            for drawer in drawers:
                try:
                    elements = drawer.find_elements(By.CSS_SELECTOR, selector)
                except WebDriverException:
                    elements = []
                for element in elements:
                    if element.is_displayed():
                        return element
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            if element.is_displayed():
                return element
    return None


def normalize_summary_text(summary: str) -> str:
    return re.sub(r"\s+", " ", summary or "").strip()


def is_element_disabled(element) -> bool:
    try:
        disabled_attr = (element.get_attribute("disabled") or "").strip().lower()
        aria_disabled = (element.get_attribute("aria-disabled") or "").strip().lower()
        classes = (element.get_attribute("class") or "").strip().lower()
    except WebDriverException:
        return False
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
        time.sleep(0.4 + (attempt * 0.2))

    if last_exc:
        raise last_exc


def set_element_text(driver, element, text: str) -> None:
    element.click()
    try:
        element.clear()
    except WebDriverException:
        pass
    element.send_keys(Keys.CONTROL, "a")
    element.send_keys(Keys.BACKSPACE)
    element.send_keys(text)
    try:
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            element,
        )
    except WebDriverException:
        pass
    try:
        element.send_keys(Keys.TAB)
    except WebDriverException:
        pass


def click_save(driver) -> None:
    try:
        save_button = driver.find_element(By.XPATH, SAVE_BUTTON_XPATH)
    except WebDriverException:
        save_button = None
    if not save_button:
        for drawer_selector in PROFILE_SUMMARY_DRAWER_SELECTORS:
            try:
                drawers = driver.find_elements(By.CSS_SELECTOR, drawer_selector)
            except WebDriverException:
                drawers = []
            for drawer in drawers:
                if not drawer.is_displayed():
                    continue
                try:
                    buttons = drawer.find_elements(By.XPATH, ".//button")
                except WebDriverException:
                    buttons = []
                for button in buttons:
                    label = (button.text or "").strip().lower()
                    if "save" in label or "update" in label:
                        save_button = button
                        break
                    btn_type = (button.get_attribute("type") or "").lower()
                    btn_class = (button.get_attribute("class") or "").lower()
                    if btn_type == "submit" and ("save" in label or "btn-dark-ot" in btn_class):
                        save_button = button
                        break
                if save_button:
                    break
            if save_button:
                break
    if not save_button:
        try:
            save_button = driver.find_element(
                By.CSS_SELECTOR,
                ".profileSummaryEdit .action button.btn-dark-ot[type='submit']",
            )
        except WebDriverException:
            save_button = None
    if not save_button:
        raise RuntimeError("Save button not found.")

    if is_element_disabled(save_button):
        try:
            WebDriverWait(driver, 10).until(lambda d: not is_element_disabled(save_button))
        except TimeoutException:
            logging.warning("Save button appears disabled; attempting click anyway.")

    robust_click(driver, save_button, timeout=10)


def wait_for_drawer_close(driver, timeout: int = 15) -> None:
    def _drawer_closed(drv):
        for selector in PROFILE_SUMMARY_DRAWER_SELECTORS:
            try:
                for element in drv.find_elements(By.CSS_SELECTOR, selector):
                    if element.is_displayed():
                        return False
            except WebDriverException:
                continue
        return True

    try:
        WebDriverWait(driver, timeout).until(_drawer_closed)
    except TimeoutException:
        logging.warning("Profile summary drawer still visible after save.")


def save_debug_artifacts(driver, label: str) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = DEBUG_DIR / f"{label}_{timestamp}.html"
    png_path = DEBUG_DIR / f"{label}_{timestamp}.png"
    try:
        html_path.write_text(driver.page_source or "", encoding="utf-8")
    except OSError:
        pass
    try:
        driver.save_screenshot(str(png_path))
    except WebDriverException:
        pass


def run_profile_update(args) -> None:
    email = args.email or os.getenv("NAUKRI_USERNAME") or os.getenv("NAUKRI_EMAIL", "")
    password = args.password or os.getenv("NAUKRI_PASSWORD", "")
    if not email or not password:
        raise RuntimeError(
            "Missing credentials. Set NAUKRI_USERNAME/NAUKRI_PASSWORD or pass --email/--password."
        )

    summary_file = Path(args.summary_file)
    day_index, selected_summary = select_daily_summary(summary_file)
    logging.info("Selected Day-%s summary from %s.", day_index, summary_file)

    html_path = Path(args.profile_html)
    baseline_summary = extract_profile_summary_from_html(html_path)
    if baseline_summary:
        logging.info("Loaded profile summary from %s.", html_path)
    else:
        logging.warning("Profile summary not found in %s.", html_path)

    driver = None
    try:
        driver = create_driver(args.headless, args.user_data_dir, args.profile_dir)
        navigate_to_profile(driver, email, password)
        current_summary = get_profile_summary_text(driver)
        normalized_current = normalize_summary_text(current_summary)
        normalized_target = normalize_summary_text(selected_summary)
        logging.info("Applying summary update (Day-%s).", day_index)

        open_profile_summary_editor(driver)
        input_element = find_summary_input(driver)
        if not input_element:
            raise RuntimeError("Profile summary input not found.")

        set_element_text(driver, input_element, selected_summary)
        if normalized_current == normalized_target:
            logging.info("Selected summary matches current content; saving to refresh update.")
        click_save(driver)
        wait_for_drawer_close(driver, timeout=20)

        logging.info("Profile summary update complete.")
    except Exception as exc:
        if driver:
            save_debug_artifacts(driver, "profile_update_error")
        raise RuntimeError(f"Profile update failed: {exc}") from exc
    finally:
        if driver:
            driver.quit()
            logging.info("Chrome driver closed.")


def parse_time_hhmm(value: str) -> Tuple[int, int]:
    match = re.match(r"^(\d{1,2}):(\d{2})$", value.strip())
    if not match:
        raise ValueError("Time must be in HH:MM format.")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Invalid time value.")
    return hour, minute


def next_run_ist(now: datetime, hour: int, minute: int) -> datetime:
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo not available; cannot compute IST schedule.")
    ist = ZoneInfo(IST_TIMEZONE)
    now_ist = now.astimezone(ist)
    target = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_ist:
        target = target + timedelta(days=1)
    return target


def schedule_loop(args) -> None:
    hour, minute = parse_time_hhmm(args.schedule_time)
    logging.info("Starting schedule loop for %s IST.", args.schedule_time)
    while True:
        next_run = next_run_ist(datetime.now().astimezone(), hour, minute)
        wait_seconds = (next_run - datetime.now().astimezone(next_run.tzinfo)).total_seconds()
        if wait_seconds > 0:
            logging.info("Sleeping for %.0f seconds until next run at %s.", wait_seconds, next_run)
            time.sleep(wait_seconds)
        run_profile_update(args)


def install_windows_task(args) -> None:
    hour, minute = parse_time_hhmm(args.schedule_time)
    python_exe = sys.executable
    script_path = Path(__file__).resolve()
    command = f'"{python_exe}" "{script_path}" --run-once'
    if args.headless:
        command += " --headless"
    if args.user_data_dir:
        command += f' --user-data-dir "{args.user_data_dir}"'
    if args.profile_dir:
        command += f' --profile-dir "{args.profile_dir}"'
    if args.profile_html:
        command += f' --profile-html "{args.profile_html}"'
    if args.summary_file:
        command += f' --summary-file "{args.summary_file}"'

    schtasks_cmd = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        args.task_name,
        "/TR",
        command,
        "/SC",
        "DAILY",
        "/ST",
        f"{hour:02d}:{minute:02d}",
    ]

    logging.info("Creating scheduled task: %s", " ".join(schtasks_cmd))
    subprocess.run(schtasks_cmd, check=True)
    logging.info("Scheduled task '%s' created.", args.task_name)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automate Naukri profile summary updates.")
    parser.add_argument("--email", help="Naukri login email (overrides NAUKRI_USERNAME/NAUKRI_EMAIL).")
    parser.add_argument("--password", help="Naukri login password (overrides NAUKRI_PASSWORD).")
    parser.add_argument("--profile-html", default=DEFAULT_PROFILE_HTML, help="Path to profile.html.")
    parser.add_argument(
        "--summary-file",
        default=DEFAULT_SUMMARY_FILE,
        help="Path to profile summaries file (default: profile_summaries.txt).",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode.")
    parser.add_argument("--user-data-dir", help="Chrome user data directory for session reuse.")
    parser.add_argument("--profile-dir", help="Chrome profile directory name.")
    parser.add_argument("--schedule-time", default=DEFAULT_SCHEDULE_TIME, help="HH:MM in IST.")
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME, help="Windows Task Scheduler name.")
    parser.add_argument("--install-task", action="store_true", help="Install daily task in Windows Task Scheduler.")
    parser.add_argument("--schedule-loop", action="store_true", help="Run a persistent scheduler loop.")
    parser.add_argument("--run-once", action="store_true", help="Run a single update and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main() -> None:
    script_env = Path(__file__).with_name(DEFAULT_ENV_FILE)
    load_env_file(script_env)
    if not script_env.exists():
        load_env_file(Path(DEFAULT_ENV_FILE))
    parser = build_arg_parser()
    args = parser.parse_args()
    if not args.headless:
        args.headless = parse_bool(os.getenv("HEADLESS"), default=False)
    setup_logging(args.verbose)

    if args.install_task:
        install_windows_task(args)
        return

    if args.schedule_loop:
        schedule_loop(args)
        return

    if args.run_once:
        run_profile_update(args)
        return

    schedule_loop(args)


if __name__ == "__main__":
    from resume_headline_updater import main as _main

    _main()
