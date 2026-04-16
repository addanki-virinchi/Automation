import os
import csv
import time
import re
import threading
from queue import Queue, Empty

import pandas as pd

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions

# Try to import webdriver_manager for automatic ChromeDriver management
try:
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False
    print("webdriver-manager not available. Install it with: pip install webdriver-manager")

csv_lock = threading.Lock()
processed_lock = threading.Lock()


# --------------------------
# Utility
# --------------------------
def extract_coordinates_from_url(url: str):
    """
    Extract latitude and longitude coordinates from Google Maps URL
    Looks for 3d<lat> and 4d<lng>
    """
    try:
        lat_pattern = r'3d([+-]?\d+\.?\d*)'
        lng_pattern = r'4d([+-]?\d+\.?\d*)'

        lat_match = re.search(lat_pattern, url)
        lng_match = re.search(lng_pattern, url)

        latitude = lat_match.group(1) if lat_match else "Not Found"
        longitude = lng_match.group(1) if lng_match else "Not Found"
        return latitude, longitude
    except Exception:
        return "Not Found", "Not Found"


def safe_driver_quit(driver):
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass


def load_processed_urls(output_filename: str) -> set:
    processed = set()
    if not os.path.exists(output_filename):
        return processed

    try:
        with open(output_filename, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                u = row.get("URL")
                if u:
                    processed.add(u)
    except Exception as e:
        print(f"Warning: could not read existing output file: {e}")

    return processed


def append_result_to_csv(result: dict, output_filename: str):
    with csv_lock:
        file_exists = os.path.exists(output_filename)

        fieldnames = [
            'URL', 'Name', 'Address', 'Website', 'Phone',
            'Store_Type', 'Operating_Status', 'Operating_Hours',
            'Rating', 'Review_Count', 'Permanently_Closed',
            'Latitude', 'Longitude'
        ]

        # Ensure all keys exist
        row = {k: result.get(k, "Not Found") for k in fieldnames}

        with open(output_filename, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists or os.path.getsize(output_filename) == 0:
                writer.writeheader()
            writer.writerow(row)


def scroll_page(driver):
    """Gentle scroll to trigger lazy-loaded elements."""
    total_height = driver.execute_script("return document.body.scrollHeight")
    step = 600

    for y in range(0, total_height, step):
        driver.execute_script(f"window.scrollTo(0, {y});")
        time.sleep(0.25)

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)


# --------------------------
# Extraction functions (mostly your logic)
# --------------------------
def extract_phone_number(driver, wait):
    try:
        scroll_page(driver)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(0.5)

        phone_xpaths = [
            "//div[contains(@class, 'AeaXub')]//div[contains(@class, 'Io6YTe') and contains(@class, 'fontBodyMedium') and contains(@class, 'kR99db')]",
            "//div[contains(@class, 'rogA2c')]//div[contains(@class, 'Io6YTe') and contains(@class, 'fontBodyMedium')]",
            "//div[contains(@class, 'Io6YTe') and contains(@class, 'kR99db')]",
            "//a[contains(@href, 'tel:')]"
        ]

        # FIXED: missing comma bug in your original list
        phone_patterns = [
            r'\+91[-.\s]?\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}',
            r'0\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}',
            r'0\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{3,4}',
            r'\d{3}[-.\s]?\d{4}[-.\s]?\d{4}',
            r'\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}',
            r'[6-9]\d{9}',
            r'\+91[-.\s]?[6-9]\d{9}'
        ]

        for xpath in phone_xpaths:
            phone_elements = driver.find_elements(By.XPATH, xpath)
            for element in phone_elements:
                try:
                    driver.execute_script("arguments[0].scrollIntoView(true);", element)
                    time.sleep(0.2)

                    if "tel:" in xpath:
                        phone_text = element.get_attribute("href") or ""
                        if phone_text.startswith("tel:"):
                            phone_text = phone_text.replace("tel:", "").strip()
                    else:
                        phone_text = element.text.strip()

                    if not phone_text:
                        continue

                    for pattern in phone_patterns:
                        m = re.findall(pattern, phone_text)
                        if m:
                            phone_number = m[0]
                            cleaned = ''.join(c for c in phone_number if c.isdigit() or c == '+')
                            digit_count = len(re.findall(r'\d', cleaned))
                            if 10 <= digit_count <= 13:
                                return cleaned

                except Exception:
                    continue

        return "Phone Number Not Found"

    except Exception:
        return "Phone Number Not Found"


def extract_store_type(driver, wait):
    try:
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.8)

        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(0.3)

        category_selectors = [
            "//button[contains(@class, 'DkEaL')]",
            "//button[contains(@class, 'DkEaL') and contains(@jsaction, 'pane.wfvdle18.category')]",
            "//button[contains(@jsaction, 'pane.wfvdle18.category')]",
            "//div[contains(@class, 'fontBodyMedium')]//button[contains(@class, 'DkEaL')]",
            "//div[contains(@class, 'LBgpqf')]//button[contains(@class, 'DkEaL')]",
            "//span[contains(@class, 'YhemCb')]",
        ]

        excluded_terms = ['directions', 'save', 'share', 'nearby', 'call', 'website', 'menu', 'order']

        for selector in category_selectors:
            elements = driver.find_elements(By.XPATH, selector)
            for el in elements:
                try:
                    driver.execute_script("arguments[0].scrollIntoView(true);", el)
                    time.sleep(0.2)
                    text = el.text.strip()
                    if text and not any(t in text.lower() for t in excluded_terms):
                        return text
                except Exception:
                    continue

    except Exception:
        pass

    return "Not Found"


def extract_operating_status_and_hours(driver, wait):
    status = "Not Found"
    operating_hours = "Not Found"

    try:
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(0.3)

        status_selectors = [
            "//span[contains(@class, 'ZDu9vd')]",
            "//div[contains(@class, 'MkV9')]//span[contains(@class, 'ZDu9vd')]",
            "//span[contains(text(), 'Open') or contains(text(), 'Closed') or contains(text(), 'Closes') or contains(text(), 'Opens')]",
            "//div[contains(@class, 'o0Svhf')]//span",
        ]

        for selector in status_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for el in elements:
                    try:
                        txt = el.text.strip()
                        if not txt:
                            continue

                        low = txt.lower()
                        if not any(k in low for k in ['open', 'closed', 'closes', 'opens']):
                            continue

                        if "closed" in low and "opens" in low:
                            status = "Open"
                            m = re.search(r'opens\s+(.+)', txt, re.IGNORECASE)
                            if m:
                                operating_hours = f"Opens {m.group(1).strip()}"
                            return status, operating_hours

                        if "open" in low and "closes" in low:
                            status = "Open now" if "open now" in low else "Open"
                            m = re.search(r'closes\s+(.+)', txt, re.IGNORECASE)
                            if m:
                                operating_hours = f"Closes {m.group(1).strip()}"
                            return status, operating_hours

                        if "open now" in low:
                            return "Open now", operating_hours
                        if "open" in low:
                            return "Open", operating_hours
                        if "closed" in low:
                            return "Closed", operating_hours
                    except Exception:
                        continue
            except Exception:
                continue

        # Try detailed hours table
        try:
            hours_table = driver.find_elements(By.XPATH, "//table[contains(@class, 'eK4R0e')]")
            if hours_table:
                rows = hours_table[0].find_elements(By.XPATH, ".//tr[contains(@class, 'y0skZc')]")
                if rows:
                    cell = rows[0].find_elements(By.XPATH, ".//td[contains(@class, 'mxowUb')]")
                    if cell:
                        hours_text = cell[0].text.strip()
                        if hours_text:
                            operating_hours = hours_text
        except Exception:
            pass

    except Exception:
        pass

    return status, operating_hours


def extract_rating(driver, wait):
    try:
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(0.2)

        rating_selectors = [
            "//div[contains(@class, 'F7nice')]//span[@aria-hidden='true']",
            "//div[contains(@jslog, '76333')]//span[@aria-hidden='true']",
        ]

        for selector in rating_selectors:
            elements = driver.find_elements(By.XPATH, selector)
            for el in elements:
                try:
                    txt = el.text.strip()
                    if not txt:
                        continue
                    if re.fullmatch(r'[0-5](?:\.[0-9])?', txt):
                        return txt
                except Exception:
                    continue
    except Exception:
        pass

    return "Not Found"


def extract_review_count(driver, wait):
    try:
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(0.2)

        review_selectors = [
            "//span[contains(@aria-label, 'review')]",
            "//div[contains(@class, 'F7nice')]//span[contains(@aria-label, 'review')]",
        ]

        for selector in review_selectors:
            elements = driver.find_elements(By.XPATH, selector)
            for el in elements:
                try:
                    aria = el.get_attribute("aria-label") or ""
                    m = re.search(r'(\d+)\s+reviews?', aria)
                    if m:
                        return m.group(1)
                    txt = el.text.strip()
                    m2 = re.search(r'\((\d+)\)', txt)
                    if m2:
                        return m2.group(1)
                except Exception:
                    continue

    except Exception:
        pass

    return "Not Found"


def extract_permanently_closed_status(driver, wait):
    try:
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(0.2)

        selectors = [
            "//div[contains(@class, 'o0Svhf')]//span[contains(text(), 'Permanently closed')]",
            "//div[contains(@class, 'o0Svhf')]//span[contains(text(), 'Temporarily closed')]",
            "//span[contains(text(), 'Permanently closed')]",
            "//span[contains(text(), 'Temporarily closed')]",
        ]

        for selector in selectors:
            els = driver.find_elements(By.XPATH, selector)
            for el in els:
                txt = (el.text or "").strip().lower()
                if "permanently closed" in txt or "temporarily closed" in txt:
                    return "Yes"
    except Exception:
        pass

    return "No"


# --------------------------
# Main scrape function (your flow, reduced sleeps)
# --------------------------
def scrape_data(url, driver, wait):
    try:
        # Force a clean navigation (prevents some state carryover)
        driver.get("about:blank")
        time.sleep(0.2)

        driver.get(url)

        # Wait for the main title of the place
        title_el = wait.until(EC.presence_of_element_located((By.XPATH, "//h1[contains(@class,'DUwDvf')]")))
        time.sleep(0.4)

        name = title_el.text.strip() if title_el else "Name Not Found"
        current_url = driver.current_url

        address = website = phone = "Not Found"

        # ✅ Address: use data-item-id (prevents grabbing nearby cards)
        try:
            address_el = driver.find_elements(By.XPATH, "//button[@data-item-id='address']//div[contains(@class,'Io6YTe')]")
            if address_el:
                address = address_el[0].text.strip()
        except Exception:
            pass

        # ✅ Website: use authority link
        try:
            website_el = driver.find_elements(By.XPATH, "//a[contains(@data-item-id,'authority') or contains(@aria-label,'Website')]")
            if website_el:
                website = website_el[0].get_attribute("href") or "Not Found"
        except Exception:
            pass

        # ✅ Phone: use phone:tel button
        try:
            phone_el = driver.find_elements(By.XPATH, "//button[contains(@data-item-id,'phone:tel')]//div[contains(@class,'Io6YTe')]")
            if phone_el:
                phone = phone_el[0].text.strip()
        except Exception:
            pass

        # Keep your other extractors (store type, status, rating, etc.)
        store_type = extract_store_type(driver, wait)
        operating_status, operating_hours = extract_operating_status_and_hours(driver, wait)
        rating = extract_rating(driver, wait)
        review_count = extract_review_count(driver, wait)
        permanently_closed = extract_permanently_closed_status(driver, wait)

        latitude, longitude = extract_coordinates_from_url(current_url)

        return {
            'URL': current_url,  # ✅ write final/current URL
            'Name': name,
            'Address': address,
            'Website': website,
            'Phone': phone,
            'Store_Type': store_type,
            'Operating_Status': operating_status,
            'Operating_Hours': operating_hours,
            'Rating': rating,
            'Review_Count': review_count,
            'Permanently_Closed': permanently_closed,
            'Latitude': latitude,
            'Longitude': longitude
        }

    except Exception as e:
        lat, lng = extract_coordinates_from_url(url)
        return {
            'URL': url,
            'Name': 'Error',
            'Address': 'Error',
            'Website': 'Error',
            'Phone': 'Error',
            'Store_Type': 'Error',
            'Operating_Status': 'Error',
            'Operating_Hours': 'Error',
            'Rating': 'Error',
            'Review_Count': 'Error',
            'Permanently_Closed': 'Error',
            'Latitude': lat,
            'Longitude': lng
        }


# --------------------------
# Driver creation (persistent per thread)
# --------------------------
def create_chrome_driver(thread_id=0):
    options = ChromeOptions()

    options.add_argument('--window-size=1920,1080')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-dev-shm-usage')

    # BIG speed-up:
    #options.add_argument("--headless=new")

    # Reduce load
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.page_load_strategy = "eager"

    # One profile per thread (persistent), NOT per URL
    base = r"C:\temp\chrome_profiles"
    os.makedirs(base, exist_ok=True)
    profile_dir = os.path.join(base, f"thread_{thread_id}")
    os.makedirs(profile_dir, exist_ok=True)
    options.add_argument(f'--user-data-dir={profile_dir}')

    if WEBDRIVER_MANAGER_AVAILABLE:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    return webdriver.Chrome(options=options)


# --------------------------
# Worker threads: each owns a driver
# --------------------------
def worker(thread_id, q: Queue, output_filename: str, processed_urls: set, stats: dict):
    driver = None
    try:
        driver = create_chrome_driver(thread_id)
        wait = WebDriverWait(driver, 15)

        while True:
            try:
                url = q.get(timeout=2)
            except Empty:
                return

            with processed_lock:
                if url in processed_urls:
                    stats["skipped"] += 1
                    q.task_done()
                    continue
                processed_urls.add(url)

            try:
                result = scrape_data(url, driver, wait)
            except Exception as e:
                lat, lng = extract_coordinates_from_url(url)
                result = {
                    'URL': url,
                    'Name': 'Error',
                    'Address': 'Error',
                    'Website': 'Error',
                    'Phone': 'Error',
                    'Store_Type': 'Error',
                    'Operating_Status': 'Error',
                    'Operating_Hours': 'Error',
                    'Rating': 'Error',
                    'Review_Count': 'Error',
                    'Permanently_Closed': 'Error',
                    'Latitude': lat,
                    'Longitude': lng
                }

            append_result_to_csv(result, output_filename)
            stats["done"] += 1

            completed = stats["done"] + stats["skipped"]
            if completed % 5 == 0:
                print(f"[Thread {thread_id}] Progress: completed={completed} done={stats['done']} skipped={stats['skipped']}")

            q.task_done()

    finally:
        safe_driver_quit(driver)


# --------------------------
# Main
# --------------------------
def main():
    input_filename = 'company_urls.csv'
    output_filename = 'company_urls_op.csv'

    if not os.path.exists(input_filename):
        print(f"Error: Input file '{input_filename}' not found!")
        return

    try:
        df = pd.read_csv(input_filename)
        if 'URL' not in df.columns:
            print(f"Error: 'URL' column not found in {input_filename}")
            print(f"Available columns: {list(df.columns)}")
            return
        urls = df['URL'].dropna().astype(str).tolist()
        urls = [u.strip() for u in urls if u.strip()]
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return

    processed_urls = load_processed_urls(output_filename)

    print(f"Loaded {len(urls)} URLs")
    print(f"Already processed: {len(processed_urls)}")

    q = Queue()
    for u in urls:
        q.put(u)

    MAX_THREADS = 2  # keep low to reduce Google rate-limits
    stats = {"done": 0, "skipped": 0}

    threads = []
    for t_id in range(MAX_THREADS):
        t = threading.Thread(target=worker, args=(t_id, q, output_filename, processed_urls, stats), daemon=True)
        t.start()
        threads.append(t)

    q.join()

    print("\nDone.")
    print(f"Processed new: {stats['done']}")
    print(f"Skipped existing: {stats['skipped']}")
    print(f"Output file: {output_filename}")


if __name__ == "__main__":
    main()
