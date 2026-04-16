"""Automate Naukri resume headline updates via Selenium."""

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None


PROFILE_URL = "https://www.naukri.com/mnjuser/profile"
LOGIN_URL = "https://www.naukri.com/nlogin/login"

DEFAULT_HEADLINE_FILE = "resume_headlines.txt"
LEGACY_HEADLINE_FILE = "profile_summaries.txt"
DEFAULT_TASK_NAME = "NaukriResumeHeadlineUpdater"

IST_TIMEZONE = "Asia/Kolkata"
DEFAULT_SCHEDULE_TIME = "14:56"
DEFAULT_ENV_FILE = ".env"

LOG_PATH = Path(__file__).with_suffix(".log")
DEBUG_DIR = Path(__file__).with_name("debug_resume_headline_editor")

LOGIN_FIELD_SELECTORS = [
    "input#usernameField",
    "input[name='email']",
    "input[type='text'][placeholder*='Email']",
]

PASSWORD_FIELD_SELECTORS = [
    "input#passwordField",
    "input[type='password']",
]

RESUME_HEADLINE_WIDGET_SELECTORS = [
    "#lazyResumeHead.resumeHeadline",
    "#lazyResumeHead",
    "div.resumeHeadline",
]

RESUME_HEADLINE_EDIT_SELECTORS = [
    "#lazyResumeHead .widgetHead .edit.icon",
    "#lazyResumeHead .widgetHead .edit",
    ".resumeHeadline .widgetHead .edit.icon",
    ".resumeHeadline .widgetHead .edit",
]

RESUME_HEADLINE_DRAWER_SELECTORS = [
    ".resumeHeadlineEdit",
    ".profileEditDrawer.resumeHeadlineEdit",
]

HEADLINE_INPUT_SELECTORS = [
    "textarea[name='resumeHeadline']",
    "textarea#resumeHeadline",
    "input[name='resumeHeadline']",
    "input[name*='headline']",
    "textarea",
    "input[type='text']",
    "div[contenteditable='true']",
    "div[role='textbox']",
]

SAVE_BUTTON_XPATH = (
    "//button[contains(@class,'btn-dark-ot') and contains(text(),'Save')]"
)

LOGIN_ERROR_KEYWORDS = [
    "invalid",
    "incorrect",
    "try again",
    "captcha",
    "verification",
    "otp",
    "access denied",
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
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


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


def wait_for_document_ready(driver, timeout: int = 20) -> None:
    try:
        WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
    except TimeoutException:
        return


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


def any_visible(parent, selectors: List[str]) -> bool:
    for selector in selectors:
        try:
            for element in parent.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    return True
        except WebDriverException:
            continue
    return False


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
    for attempt in range(5):
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


def page_contains_login_issue(driver) -> bool:
    try:
        snippet = driver.execute_script(
            "return document.body && document.body.innerText ? document.body.innerText.slice(0, 2000) : ''"
        )
    except WebDriverException:
        return False
    text = (snippet or "").lower()
    return any(keyword in text for keyword in LOGIN_ERROR_KEYWORDS)


def detect_login_form(driver) -> bool:
    return any(driver.find_elements(By.CSS_SELECTOR, sel) for sel in LOGIN_FIELD_SELECTORS)


def login_if_needed(driver, email: str, password: str) -> bool:
    if not detect_login_form(driver):
        return True
    logging.info("Login form detected. Attempting login.")
    email_field = find_first(driver, LOGIN_FIELD_SELECTORS)
    password_field = find_first(driver, PASSWORD_FIELD_SELECTORS)
    if not email_field or not password_field:
        logging.warning("Login fields not found.")
        return False
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
    if submit_btn:
        try:
            robust_click(driver, submit_btn, timeout=10)
        except WebDriverException:
            try:
                driver.execute_script("arguments[0].click();", submit_btn)
            except WebDriverException:
                password_field.send_keys(Keys.ENTER)
    else:
        password_field.send_keys(Keys.ENTER)

    wait_for_document_ready(driver, timeout=30)
    time.sleep(2.0)
    if detect_login_form(driver) or page_contains_login_issue(driver):
        logging.warning("Login appears unsuccessful (captcha/OTP/invalid credentials).")
        return False
    return True


def has_resume_headline_widget(driver) -> bool:
    return any(driver.find_elements(By.CSS_SELECTOR, sel) for sel in RESUME_HEADLINE_WIDGET_SELECTORS)


def scroll_to_resume_headline(driver) -> bool:
    try:
        return bool(
            driver.execute_script(
                "var el = document.getElementById('lazyResumeHead') || document.querySelector('.resumeHeadline');"
                "if (el) { el.scrollIntoView({block: 'center'}); return true; } return false;"
            )
        )
    except WebDriverException:
        return False


def navigate_to_profile(driver, email: str, password: str) -> bool:
    last_error: Optional[str] = None
    for attempt in range(2):
        try:
            driver.get(PROFILE_URL)
        except TimeoutException:
            pass
        wait_for_document_ready(driver, timeout=30)
        time.sleep(1.5)

        if detect_login_form(driver) or "login" in (driver.current_url or "").lower():
            logging.info("Redirected to login page.")
            if not detect_login_form(driver):
                try:
                    driver.get(LOGIN_URL)
                except TimeoutException:
                    pass
                wait_for_document_ready(driver, timeout=20)
            if not login_if_needed(driver, email, password):
                last_error = "Login failed."
                continue
            try:
                driver.get(PROFILE_URL)
            except TimeoutException:
                pass
            wait_for_document_ready(driver, timeout=30)
            time.sleep(1.5)

        if page_contains_login_issue(driver):
            last_error = "Profile page blocked (captcha/OTP/access denied)."
            continue

        if has_resume_headline_widget(driver):
            return True

        scroll_to_resume_headline(driver)
        time.sleep(0.8)
        if has_resume_headline_widget(driver):
            return True

        last_error = "Resume headline widget not visible after navigation."
        if attempt == 0:
            continue

    if last_error:
        logging.warning("%s", last_error)
    return False


def open_resume_headline_editor(driver) -> bool:
    if any_visible(driver, RESUME_HEADLINE_DRAWER_SELECTORS):
        return True

    scroll_to_resume_headline(driver)
    time.sleep(0.8)

    edit_icon = find_first(driver, RESUME_HEADLINE_EDIT_SELECTORS)
    if not edit_icon:
        logging.warning("Resume headline edit icon not found.")
        return False
    try:
        robust_click(driver, edit_icon, timeout=10)
    except WebDriverException:
        try:
            driver.execute_script("arguments[0].click();", edit_icon)
        except WebDriverException:
            return False

    try:
        WebDriverWait(driver, 12).until(lambda d: any_visible(d, RESUME_HEADLINE_DRAWER_SELECTORS))
    except TimeoutException:
        logging.warning("Resume headline editor drawer did not open.")
        return False
    return True


def find_drawer(driver):
    for selector in RESUME_HEADLINE_DRAWER_SELECTORS:
        try:
            for drawer in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if drawer.is_displayed():
                        return drawer
                except WebDriverException:
                    continue
        except WebDriverException:
            continue
    return None


def find_headline_input(driver):
    drawer = find_drawer(driver)
    scopes = [drawer] if drawer is not None else [driver]
    for scope in scopes:
        for selector in HEADLINE_INPUT_SELECTORS:
            try:
                elements = scope.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            for element in elements:
                try:
                    if element.is_displayed():
                        return element
                except WebDriverException:
                    continue
    return None


def set_element_text_exact(driver, element, text: str) -> None:
    element.click()
    try:
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
    except WebDriverException:
        pass

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


def click_save(driver) -> bool:
    drawer = find_drawer(driver)
    if drawer is None:
        logging.warning("Save drawer scope not found.")
        return False

    try:
        buttons = drawer.find_elements(By.XPATH, ".//button")
    except WebDriverException:
        buttons = []
    save_button = None
    for button in buttons:
        try:
            label = (button.text or "").strip().lower()
            btn_type = (button.get_attribute("type") or "").lower()
            btn_class = (button.get_attribute("class") or "").lower()
        except WebDriverException:
            continue
        if "save" in label or "update" in label:
            save_button = button
            break
        if btn_type == "submit" and ("btn-dark-ot" in btn_class):
            save_button = button
            break

    if save_button is None:
        try:
            save_button = drawer.find_element(By.XPATH, SAVE_BUTTON_XPATH)
        except WebDriverException:
            save_button = None
    if save_button is None:
        logging.warning("Save button not found in resume headline drawer.")
        return False

    if is_element_disabled(save_button):
        try:
            WebDriverWait(driver, 10).until(lambda d: not is_element_disabled(save_button))
        except TimeoutException:
            logging.warning("Save button appears disabled; attempting click anyway.")

    try:
        robust_click(driver, save_button, timeout=10)
        return True
    except WebDriverException:
        return False


def wait_for_drawer_close(driver, timeout: int = 15) -> None:
    def _drawer_closed(drv):
        return not any_visible(drv, RESUME_HEADLINE_DRAWER_SELECTORS)

    try:
        WebDriverWait(driver, timeout).until(_drawer_closed)
    except TimeoutException:
        logging.warning("Resume headline drawer still visible after save.")


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


def load_headline_lines(path: Path) -> Dict[int, str]:
    if not path.exists():
        return {}
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {}

    day_map: Dict[int, str] = {}
    fallback: List[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^day[- ]?(\d)\s*:\s*(.+)$", stripped, flags=re.IGNORECASE)
        if match:
            day_map[int(match.group(1))] = match.group(2).strip()
        else:
            fallback.append(stripped)

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


def select_daily_headline(path: Path) -> Tuple[int, str]:
    day_map = load_headline_lines(path)
    day_index = current_day_index_ist()
    headline = day_map.get(day_index)
    if not headline:
        raise RuntimeError(f"No headline found for Day-{day_index} in {path}.")
    return day_index, headline


def run_headline_update(args) -> bool:
    email = args.email or os.getenv("NAUKRI_USERNAME") or os.getenv("NAUKRI_EMAIL", "")
    password = args.password or os.getenv("NAUKRI_PASSWORD", "")
    if not email or not password:
        logging.error("Missing credentials. Set NAUKRI_USERNAME/NAUKRI_PASSWORD or pass --email/--password.")
        return False

    if args.headline:
        day_index = current_day_index_ist()
        headline = args.headline.strip()
    else:
        headline_file = Path(args.headline_file)
        if not headline_file.exists() and Path(LEGACY_HEADLINE_FILE).exists():
            headline_file = Path(LEGACY_HEADLINE_FILE)
        try:
            day_index, headline = select_daily_headline(headline_file)
        except Exception as exc:
            logging.error("%s", exc)
            return False

    if not headline:
        logging.error("Empty resume headline provided.")
        return False

    driver = None
    try:
        driver = create_driver(args.headless, args.user_data_dir, args.profile_dir)
        if not navigate_to_profile(driver, email, password):
            save_debug_artifacts(driver, "navigate_failed")
            return False

        if not open_resume_headline_editor(driver):
            save_debug_artifacts(driver, "drawer_open_failed")
            return False

        input_element = find_headline_input(driver)
        if not input_element:
            logging.warning("Resume headline input not found.")
            save_debug_artifacts(driver, "input_not_found")
            return False

        logging.info("Applying resume headline update (Day-%s).", day_index)
        set_element_text_exact(driver, input_element, headline)

        if not click_save(driver):
            save_debug_artifacts(driver, "save_click_failed")
            return False

        wait_for_drawer_close(driver, timeout=20)
        logging.info("Resume headline update complete.")
        return True

    except Exception as exc:
        if driver:
            save_debug_artifacts(driver, "headline_update_error")
        if args.verbose:
            logging.exception("Resume headline update failed.")
        else:
            logging.error("Resume headline update failed: %s", exc)
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except WebDriverException:
                pass


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
        run_headline_update(args)


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
    if args.headline:
        command += f' --headline "{args.headline}"'
    if args.headline_file:
        command += f' --headline-file "{args.headline_file}"'

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
    parser = argparse.ArgumentParser(description="Automate Naukri resume headline updates.")
    parser.add_argument("--email", help="Naukri login email (overrides NAUKRI_USERNAME/NAUKRI_EMAIL).")
    parser.add_argument("--password", help="Naukri login password (overrides NAUKRI_PASSWORD).")
    parser.add_argument("--headline", help="Resume headline text to set (overrides headline file).")
    parser.add_argument(
        "--headline-file",
        default=DEFAULT_HEADLINE_FILE,
        help=f"Path to resume headlines file (default: {DEFAULT_HEADLINE_FILE}).",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode.")
    parser.add_argument("--user-data-dir", help="Chrome user data directory for session reuse.")
    parser.add_argument("--profile-dir", help="Chrome profile directory name.")
    parser.add_argument("--schedule-time", default=DEFAULT_SCHEDULE_TIME, help="HH:MM in IST.")
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME, help="Windows Task Scheduler name.")
    parser.add_argument("--install-task", action="store_true", help="Install daily task in Windows Task Scheduler.")
    parser.add_argument("--schedule-loop", action="store_true", help="Run a persistent scheduler loop.")
    parser.add_argument("--run-once", action="store_true", help="Run a single update and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging (includes tracebacks).")
    return parser


def main() -> None:
    script_env = Path(__file__).with_name(DEFAULT_ENV_FILE)
    load_env_file(script_env)
    if not script_env.exists():
        load_env_file(Path(DEFAULT_ENV_FILE))

    parser = build_arg_parser()
    args = parser.parse_args()
    if not args.headless:
        args.headless = parse_bool(os.getenv("HEADLESS") or os.getenv("headless"), default=True)

    setup_logging(args.verbose)

    try:
        if args.install_task:
            install_windows_task(args)
            return
        if args.schedule_loop:
            schedule_loop(args)
            return
        # Default behavior: run immediately (no time-based scheduling).
        ok = run_headline_update(args)
        raise SystemExit(0 if ok else 1)
    except SystemExit:
        raise
    except Exception as exc:
        if args.verbose:
            logging.exception("Fatal error.")
        else:
            logging.error("Fatal error: %s", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
