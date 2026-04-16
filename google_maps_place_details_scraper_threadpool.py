
    
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
import time
import csv
import pandas as pd
import re
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess

# Try to import webdriver_manager for automatic ChromeDriver management
try:
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False
    print("webdriver-manager not available. Install it with: pip install webdriver-manager")

# Global lock for thread-safe CSV writing
csv_lock = threading.Lock()

def get_chrome_version():
    """Get the installed Chrome browser version for better compatibility"""
    try:
        # Try different methods to get Chrome version
        methods = [
            # Windows
            r'reg query "HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon" /v version',
            # Alternative Windows method
            r'powershell "Get-ItemProperty HKLM:\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\* | Select-Object DisplayName, DisplayVersion | Where-Object {$_.DisplayName -like \"*Chrome*\"}"',
        ]

        for method in methods:
            try:
                result = subprocess.run(method, shell=True, capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and result.stdout:
                    # Extract version number from output
                    version_match = re.search(r'(\d+)\.(\d+)\.(\d+)\.(\d+)', result.stdout)
                    if version_match:
                        major_version = int(version_match.group(1))
                        return major_version
            except:
                continue

        # Fallback: Try to get version from Chrome executable
        import platform
        if platform.system() == "Windows":
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
            for path in chrome_paths:
                if os.path.exists(path):
                    try:
                        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                        version_match = re.search(r'(\d+)\.(\d+)\.(\d+)\.(\d+)', result.stdout)
                        if version_match:
                            return int(version_match.group(1))
                    except:
                        continue

    except Exception as e:
        print(f"Warning: Could not detect Chrome version: {e}")

    return None

def extract_coordinates_from_url(url):
    """
    Extract latitude and longitude coordinates from Google Maps URL

    Args:
        url (str): Google Maps URL containing coordinates

    Returns:
        tuple: (latitude, longitude) as strings, or ("Not Found", "Not Found") if not found
    """
    try:
        # Use regex to find latitude (3d parameter) and longitude (4d parameter)
        lat_pattern = r'3d([+-]?\d+\.?\d*)'
        lng_pattern = r'4d([+-]?\d+\.?\d*)'

        lat_match = re.search(lat_pattern, url)
        lng_match = re.search(lng_pattern, url)

        latitude = lat_match.group(1) if lat_match else "Not Found"
        longitude = lng_match.group(1) if lng_match else "Not Found"

        return latitude, longitude

    except Exception as e:
        print(f"Error extracting coordinates from URL: {str(e)}")
        return "Not Found", "Not Found"

def safe_driver_quit(driver):
    """Safely quit the Chrome driver with error handling"""
    if not driver:
        return

    try:
        # First try to close all windows
        try:
            driver.close()
        except:
            pass

        # Then quit the driver
        driver.quit()
    except (OSError, Exception) as e:
        print(f"Warning: Error during driver.quit(): {e}")
        try:
            # Try to terminate the process directly
            if hasattr(driver, 'service') and driver.service and hasattr(driver.service, 'process') and driver.service.process:
                driver.service.process.terminate()
                driver.service.process.wait(timeout=5)
        except Exception as e2:
            print(f"Warning: Error terminating process: {e2}")
            try:
                # Force kill as last resort
                if hasattr(driver, 'service') and driver.service and hasattr(driver.service, 'process') and driver.service.process:
                    driver.service.process.kill()
            except:
                pass

def append_result_to_csv(result, output_filename, write_header=False):
    """
    Thread-safe append of a single result to the output CSV file immediately
    """
    try:
        with csv_lock:  # Thread-safe CSV writing
            with open(output_filename, 'a', newline='', encoding='utf-8') as file:
                fieldnames = ['URL', 'Name', 'Address', 'Website', 'Phone', 'Store_Type', 'Operating_Status', 'Operating_Hours', 'Rating', 'Review_Count', 'Permanently_Closed', 'Latitude', 'Longitude']
                writer = csv.DictWriter(file, fieldnames=fieldnames)

                # Write header only if this is the first write
                if write_header:
                    writer.writeheader()

                # Write the data row
                writer.writerow(result)

        return True
    except Exception as e:
        print(f"Error writing to CSV: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_url_already_processed(url, output_filename):
    """
    Check if a URL has already been processed by reading the existing output file
    """
    if not os.path.exists(output_filename):
        return False

    try:
        with open(output_filename, 'r', newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row.get('URL') == url:
                    return True
    except Exception as e:
        print(f"Warning: Error checking processed URLs: {e}")

    return False

def scroll_page(driver):
    """
    Scroll the page to help reveal dynamic content
    """
    # Get the total height of the page
    total_height = driver.execute_script("return document.body.scrollHeight")
    
    # Scroll in increments
    for i in range(0, total_height, 500):
        driver.execute_script(f"window.scrollTo(0, {i});")
        time.sleep(0.5)
    
    # Scroll back to top
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)


    """Extract phone number using exact Google Maps HTML structure"""
def extract_phone_number(driver, wait):
    """Extract phone number using exact Google Maps HTML structure"""
    try:
        # Scroll and wait for page to be fully loaded
        scroll_page(driver)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(1)

        # Exact XPath selectors based on confirmed HTML structure
        phone_xpaths = [
            # Most specific - targets the exact phone div structure
            "//div[contains(@class, 'AeaXub')]//div[contains(@class, 'Io6YTe') and contains(@class, 'fontBodyMedium') and contains(@class, 'kR99db')]",

            # Parent-child relationship targeting phone container
            "//div[contains(@class, 'rogA2c')]//div[contains(@class, 'Io6YTe') and contains(@class, 'fontBodyMedium')]",

            # Class combination for phone text element
            "//div[contains(@class, 'Io6YTe') and contains(@class, 'kR99db')]",

            # Fallback for tel: links
            "//a[contains(@href, 'tel:')]"
        ]

        for xpath in phone_xpaths:
            try:
                phone_elements = driver.find_elements(By.XPATH, xpath)

                for element in phone_elements:
                    try:
                        # Scroll to element to ensure it's visible
                        driver.execute_script("arguments[0].scrollIntoView(true);", element)
                        time.sleep(0.3)

                        # Extract text or href for tel: links
                        if "tel:" in xpath:
                            phone_text = element.get_attribute("href")
                            if phone_text and phone_text.startswith("tel:"):
                                phone_text = phone_text.replace("tel:", "").strip()
                        else:
                            phone_text = element.text.strip()

                        if phone_text:
                            # Phone number regex patterns for Indian numbers
                            phone_patterns = [
                                r'\+91[-.\s]?\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}',  # +91 format with spaces
                                r'0\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}',  # 0 prefix format (like 044 2522 2944)
                                r'0\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{3,4}'
                                r'\d{3}[-.\s]?\d{4}[-.\s]?\d{4}',  # 3-4-4 format
                                r'\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}',  # General landline format
                                r'[6-9]\d{9}',  # 10-digit mobile format
                                r'\+91[-.\s]?[6-9]\d{9}'  # +91 mobile format
                            ]

                            for pattern in phone_patterns:
                                phone_matches = re.findall(pattern, phone_text)
                                if phone_matches:
                                    phone_number = phone_matches[0]
                                    # Clean the phone number (keep digits and + only)
                                    cleaned_number = ''.join(c for c in phone_number if c.isdigit() or c == '+')

                                    # Validate length (Indian numbers: 10-13 digits)
                                    digit_count = len(re.findall(r'\d', cleaned_number))
                                    if 11 <= digit_count <= 13:
                                        return cleaned_number
                    except Exception:
                        continue

            except (NoSuchElementException, TimeoutException):
                continue

        return "Phone Number Not Found"

    except Exception as e:
        print(f"Error in phone extraction: {str(e)}")
        return "Phone Number Not Found"

def extract_store_type(driver, wait):
    """Extract business category/store type from Google Maps page"""
    try:
        # Enhanced scrolling and waiting for elements to load
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)

        # Wait for page to be fully loaded
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(1)

        # Try multiple selectors for store type/category (prioritized by reliability)
        category_selectors = [
            "//button[contains(@class, 'DkEaL')]",  # Most reliable - confirmed working
            "//button[contains(@class, 'DkEaL') and contains(@jsaction, 'pane.wfvdle18.category')]",
            "//button[contains(@jsaction, 'pane.wfvdle18.category')]",
            "//div[contains(@class, 'fontBodyMedium')]//button[contains(@class, 'DkEaL')]",
            "//div[contains(@class, 'LBgpqf')]//button[contains(@class, 'DkEaL')]",
            "//span[contains(@class, 'YhemCb')]",
            "//div[contains(@class, 'LBgpqf')]//button",
            "//button[contains(@aria-label, 'Category')]",
        ]

        for selector in category_selectors:
            try:
                # Find elements directly (find_elements never throws exception)
                category_elements = driver.find_elements(By.XPATH, selector)

                for element in category_elements:
                    try:
                        # Scroll to element to ensure it's visible
                        driver.execute_script("arguments[0].scrollIntoView(true);", element)
                        time.sleep(0.5)

                        category_text = element.text.strip()

                        if category_text and len(category_text) > 0:
                            # Filter out common non-category buttons
                            # IMPORTANT: "book" should NOT be in excluded_terms as "Book store" is a valid category
                            excluded_terms = ['directions', 'save', 'share', 'nearby', 'call', 'website', 'menu', 'order']
                            if not any(term in category_text.lower() for term in excluded_terms):
                                return category_text
                    except Exception:
                        continue

            except (NoSuchElementException, TimeoutException):
                continue

    except Exception as e:
        print(f"Error extracting store type: {str(e)}")

    return "Not Found"


def extract_operating_status_and_hours(driver, wait):
    """Extract operating status and hours from Google Maps page"""
    try:
        status = "Not Found"
        operating_hours = "Not Found"

        # Enhanced scrolling and waiting
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        time.sleep(2)

        # Wait for page to be fully loaded
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(1)

        # Try to find operating status with prioritized selectors (confirmed working)
        status_selectors = [
            "//span[contains(@class, 'ZDu9vd')]",  # Most reliable - confirmed working
            "//div[contains(@class, 'MkV9')]//span[contains(@class, 'ZDu9vd')]",
            "//span[contains(text(), 'Open') or contains(text(), 'Closed') or contains(text(), 'Closes')]",
            "//div[contains(@class, 'o0Svhf')]//span",
            "//span[contains(@class, 'ZDu9vd')]//span",
            "//div[contains(@aria-expanded, 'true')]//span[contains(@class, 'ZDu9vd')]"
        ]

        for selector in status_selectors:
            try:
                # Use WebDriverWait for better reliability
                status_elements = wait.until(lambda d: d.find_elements(By.XPATH, selector))

                for element in status_elements:
                    try:
                        # Scroll to element to ensure it's visible
                        driver.execute_script("arguments[0].scrollIntoView(true);", element)
                        time.sleep(0.5)

                        status_text = element.text.strip()

                        if status_text and any(keyword in status_text.lower() for keyword in ['open', 'closed', 'closes', 'opens']):
                            # Enhanced parsing logic with correct business logic
                            status_text_lower = status_text.lower()

                            # CRITICAL: If operating hours exist, business is operational (status = "Open")
                            # Only set status to "Closed" if NO operating hours are found

                            # Handle "Closed ⋅ Opens 8 am" format - business is OPEN (has operating hours)
                            if "closed" in status_text_lower and "opens" in status_text_lower:
                                status = "Open"  # Business is operational (has hours)
                                # Extract opening time
                                opens_patterns = [
                                    r'opens\s+(.+?)(?:\s+\w{3})?$',  # "Opens 8 am Tue" -> "8 am"
                                    r'opens\s+(.+)',  # General opens pattern
                                ]
                                for pattern in opens_patterns:
                                    opens_match = re.search(pattern, status_text, re.IGNORECASE)
                                    if opens_match:
                                        time_part = opens_match.group(1).strip()
                                        # Remove day abbreviations (Mon, Tue, etc.)
                                        time_part = re.sub(r'\s+\w{3}$', '', time_part).strip()
                                        operating_hours = f"Opens {time_part}"
                                        break
                                # Return immediately after successful parsing
                                return status, operating_hours

                            # Handle "Open ⋅ Closes 9 pm" format - business is OPEN
                            elif "open" in status_text_lower and "closes" in status_text_lower:
                                if "open now" in status_text_lower:
                                    status = "Open now"
                                else:
                                    status = "Open"
                                # Extract closing time
                                closes_patterns = [
                                    r'closes\s+(.+?)(?:\s+\w{3})?$',  # "Closes 9 pm" -> "9 pm"
                                    r'closes\s+(.+)',  # General closes pattern
                                ]
                                for pattern in closes_patterns:
                                    closes_match = re.search(pattern, status_text, re.IGNORECASE)
                                    if closes_match:
                                        time_part = closes_match.group(1).strip()
                                        # Remove day abbreviations
                                        time_part = re.sub(r'\s+\w{3}$', '', time_part).strip()
                                        operating_hours = f"Closes {time_part}"
                                        break
                                # Return immediately after successful parsing
                                return status, operating_hours

                            # Handle simple "Open now" or "Open" status
                            elif "open now" in status_text_lower:
                                status = "Open now"
                                # Return immediately
                                return status, operating_hours
                            elif "open" in status_text_lower:
                                status = "Open"
                                # Return immediately
                                return status, operating_hours

                            # Only set to "Closed" if no operating hours are found
                            elif "closed" in status_text_lower and "opens" not in status_text_lower:
                                status = "Closed"
                                # Return immediately
                                return status, operating_hours
                    except Exception:
                        continue

                if status != "Not Found":
                    break

            except (NoSuchElementException, TimeoutException):
                continue

        # Try to extract detailed operating hours from the hours table
        if operating_hours == "Not Found":
            try:
                # Look for the hours table with multiple selectors
                hours_selectors = [
                    "//table[contains(@class, 'eK4R0e')]",
                    "//div[contains(@class, 't39EBf')]//table",
                    "//table//tr[contains(@class, 'y0skZc')]"
                ]

                for table_selector in hours_selectors:
                    try:
                        if "//table" in table_selector:
                            hours_table = driver.find_element(By.XPATH, table_selector)
                            if hours_table:
                                # Get today's hours (first row that's not a header)
                                today_row = hours_table.find_element(By.XPATH, ".//tr[contains(@class, 'y0skZc')][1]")
                                if today_row:
                                    hours_cell = today_row.find_element(By.XPATH, ".//td[contains(@class, 'mxowUb')]")
                                    if hours_cell:
                                        hours_text = hours_cell.text.strip()
                                        if hours_text and ("–" in hours_text or "-" in hours_text):
                                            operating_hours = hours_text
                                            break
                        else:
                            # Direct row selector
                            today_rows = driver.find_elements(By.XPATH, table_selector)
                            if today_rows:
                                for row in today_rows[:1]:  # Take first row
                                    hours_cell = row.find_element(By.XPATH, ".//td[contains(@class, 'mxowUb')]")
                                    if hours_cell:
                                        hours_text = hours_cell.text.strip()
                                        if hours_text and ("–" in hours_text or "-" in hours_text):
                                            operating_hours = hours_text
                                            break
                    except (NoSuchElementException, TimeoutException):
                        continue

                    if operating_hours != "Not Found":
                        break

            except (NoSuchElementException, TimeoutException):
                pass

    except Exception as e:
        print(f"Error extracting operating status and hours: {str(e)}")

    return status, operating_hours


def extract_rating(driver, wait):
    """Extract rating and review count from Google Maps page"""
    try:
        # Enhanced scrolling and waiting for rating elements
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)

        # Wait for page to be fully loaded
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(1)

        # Try multiple selectors for rating (prioritized by reliability)
        rating_selectors = [
            "//div[contains(@class, 'F7nice')]//span[@aria-hidden='true']",  # Most reliable - confirmed working
            "//span[contains(@class, 'ceNzKf')]/preceding-sibling::span[@aria-hidden='true']",
            "//div[contains(@jslog, '76333')]//span[@aria-hidden='true']",
            "//div[contains(@class, 'F7nice')]//span[1]",
            "//span[@aria-hidden='true' and string-length(text()) <= 3]",
            "//div[contains(@class, 'jANrlb')]//div[contains(@class, 'F7nice')]//span"
        ]

        for selector in rating_selectors:
            try:
                # Use WebDriverWait for better reliability
                rating_elements = wait.until(lambda d: d.find_elements(By.XPATH, selector))

                for element in rating_elements:
                    try:
                        # Scroll to element to ensure it's visible
                        driver.execute_script("arguments[0].scrollIntoView(true);", element)
                        time.sleep(0.5)

                        rating_text = element.text.strip()

                        if rating_text:
                            # Validate that it's a numeric rating
                            if rating_text.replace('.', '').replace(',', '').isdigit():
                                try:
                                    rating_value = float(rating_text.replace(',', '.'))
                                    if 0 <= rating_value <= 5:  # Valid rating range
                                        return rating_text
                                except ValueError:
                                    continue

                            # Also check for patterns like "4.5" or "5.0"
                            rating_match = re.search(r'^([0-5](?:\.[0-9])?)$', rating_text)
                            if rating_match:
                                return rating_match.group(1)
                    except Exception:
                        continue

            except (NoSuchElementException, TimeoutException):
                continue

    except Exception as e:
        print(f"Error extracting rating: {str(e)}")

    return "Not Found"


def extract_review_count(driver, wait):
    """Extract review count from Google Maps page"""
    try:
        # Enhanced scrolling and waiting for review elements
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        # Wait for page to be fully loaded
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(1)

        # Try multiple selectors for review count (prioritized by reliability)
        review_selectors = [
            "//span[contains(@aria-label, 'review')]",  # Most reliable - targets aria-label with "review"
            "//div[contains(@class, 'F7nice')]//span[contains(@aria-label, 'review')]",  # Within rating section
            "//span[contains(@aria-label, 'reviews')]",  # Plural form
            "//span[contains(@aria-label, 'review') and contains(text(), '(')]",  # With parentheses
            "//div[contains(@jslog, '76333')]//span[contains(@aria-label, 'review')]"  # Within rating container
        ]

        for selector in review_selectors:
            try:
                # Find elements directly (find_elements never throws exception)
                review_elements = driver.find_elements(By.XPATH, selector)

                for element in review_elements:
                    try:
                        # Scroll to element to ensure it's visible
                        driver.execute_script("arguments[0].scrollIntoView(true);", element)
                        time.sleep(0.3)

                        # Extract aria-label attribute
                        aria_label = element.get_attribute("aria-label")

                        if aria_label:
                            # Extract number from aria-label like "40 reviews" or "1 review"
                            review_match = re.search(r'(\d+)\s+reviews?', aria_label)
                            if review_match:
                                review_count = review_match.group(1)
                                # Validate that it's a numeric value
                                if review_count.isdigit():
                                    return review_count

                        # Also try extracting from visible text in parentheses
                        review_text = element.text.strip()
                        if review_text:
                            # Extract number from text like "(40)" or "(150)"
                            text_match = re.search(r'\((\d+)\)', review_text)
                            if text_match:
                                review_count = text_match.group(1)
                                if review_count.isdigit():
                                    return review_count

                    except Exception:
                        continue

            except (NoSuchElementException, TimeoutException):
                continue

        return "Not Found"

    except Exception as e:
        print(f"Error extracting review count: {str(e)}")
        return "Not Found"


def extract_permanently_closed_status(driver, wait):
    """Check if business is permanently closed"""
    try:
        # Look for permanently closed indicator    
           # <div class="o0Svhf"><span class="ZDu9vd"><span class="aSftqf ">Permanently closed</span></span> <span class="puWIL hKrmvd google-symbols JUDSqb" aria-label="Show details" role="img" style="font-size: 18px;"></span></div>
        closed_selectors = [
                "//div[contains(@class, 'o0Svhf')]//span[contains(text(), 'Permanently closed')]",
                "//div[contains(@class, 'o0Svhf')]//span[contains(text(), 'Temporarily closed')]" ]


        for selector in closed_selectors:
            try:
                closed_element = driver.find_element(By.XPATH, selector)
                if closed_element and "Permanently closed" or "Temporarily closed" in closed_element.text:
                    return "Yes"
            except (NoSuchElementException, TimeoutException):
                continue

    except Exception as e:
        print(f"Error checking permanently closed status: {str(e)}")

    return "No"


def scrape_data(url, driver, wait):
    try:
        # Navigate to the URL
        driver.get(url)
        time.sleep(8)  # Increased wait time for page to load completely

        # Wait for page to be fully loaded
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)

        # Scroll the page to ensure all elements are loaded
        scroll_page(driver)
        time.sleep(3)  # Additional wait after scrolling

        # Additional scroll to ensure dynamic content is loaded
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)

        # Initialize variables with default values
        address = website = phone = "Not Found"

        try:
            # Address extraction
            address_element = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//div[contains(@class,'rogA2c')]/div[contains(@class,'Io6YTe')]")
            ))
            # Scroll to address element
            driver.execute_script("arguments[0].scrollIntoView(true);", address_element)
            address = address_element.text
        except (TimeoutException, NoSuchElementException):
            pass

        try:
            # Website extraction
            website_element = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//a[contains(@aria-label, 'Website')]")
            ))
            website = website_element.get_attribute("href")
        except (TimeoutException, NoSuchElementException):

            pass
        try:
            name_element = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//h1[contains(@class, 'DUwDvf lfPIob')]")
            ))
            driver.execute_script("arguments[0].scrollIntoView(true);", name_element)
            name = name_element.text.strip()
        except (TimeoutException, NoSuchElementException):
            name = "Name Not Found"

        # Phone number extraction
        phone = extract_phone_number(driver, wait)

        # Extract new business information
        store_type = extract_store_type(driver, wait)
        operating_status, operating_hours = extract_operating_status_and_hours(driver, wait)
        rating = extract_rating(driver, wait)
        review_count = extract_review_count(driver, wait)
        permanently_closed = extract_permanently_closed_status(driver, wait)

        # Coordinate extraction from URL
        latitude, longitude = extract_coordinates_from_url(url)

        return {
            'URL': url,
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

    except WebDriverException as e:
        print(f"Error processing URL {url}: {str(e)}")
        # Even if scraping fails, we can still extract coordinates from the URL
        latitude, longitude = extract_coordinates_from_url(url)
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
            'Latitude': latitude,
            'Longitude': longitude
        }

def main():
    # Check if the input CSV file exists
    input_filename = 'company_urls.csv'
    if not os.path.exists(input_filename):
        print(f"Error: Input file '{input_filename}' not found!")
        print("Please make sure you have run the Google_Maps.py script first to generate the master CSV file.")
        print("The file should be created by the Google Maps scraper with the correct spelling: 'stationery' not 'stationary'")
        return

    # Read URLs from CSV file
    try:
        df = pd.read_csv(input_filename)
        if 'URL' not in df.columns:
            print(f"Error: 'URL' column not found in {input_filename}")
            print(f"Available columns: {list(df.columns)}")
            return

        urls = df['URL'].tolist()
        print(f"Successfully loaded {len(urls)} URLs from {input_filename}")
    except Exception as e:
        print(f"Error reading CSV file: {str(e)}")
        return

    # Setup output file for real-time incremental writing
    output_filename = 'company_urls_op.csv'

    # Check if output file already exists to determine if we need to write header
    file_exists = os.path.exists(output_filename)

    # Count existing processed URLs for resume capability
    processed_count = 0
    if file_exists:
        try:
            with open(output_filename, 'r', newline='', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                processed_count = sum(1 for row in reader)
            print(f"Found existing output file with {processed_count} processed URLs")
            print("Will skip already processed URLs and continue from where left off")
        except Exception as e:
            print(f"Warning: Error reading existing output file: {e}")

    # Continue with multithreaded processing
    process_urls_multithreaded(urls, output_filename, file_exists)

def process_urls_multithreaded(urls, output_filename, file_exists):
    """
    Process URLs using multithreading for improved performance
    """
    # Multithreading configuration
    MAX_THREADS = 2  # Conservative number to avoid overwhelming Google Maps

    # Counters for real-time progress tracking
    total_urls = len(urls)
    processed_new = 0
    skipped_existing = 0
    errors = 0

    print(f"\n{'='*80}")
    print(f"STARTING MULTITHREADED REAL-TIME INCREMENTAL DATA EXTRACTION")
    print(f"{'='*80}")
    print(f"Total URLs to process: {total_urls}")
    print(f"Output file: {output_filename}")
    print(f"Threads: {MAX_THREADS}")
    print(f"Mode: Real-time incremental CSV writing with coordinates")
    print("-" * 80)

    # Write header if file doesn't exist
    if not file_exists:
        try:
            with open(output_filename, 'w', newline='', encoding='utf-8') as file:
                fieldnames = ['URL', 'Name', 'Address', 'Website', 'Phone', 'Store_Type', 'Operating_Status', 'Operating_Hours', 'Rating', 'Review_Count', 'Permanently_Closed', 'Latitude', 'Longitude']
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
            print("✅ Created output file with headers")
        except Exception as e:
            print(f"❌ Error creating output file: {e}")
            return

    try:
        # Process URLs using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            # Submit all URL processing tasks
            future_to_url = {}
            for index, url in enumerate(urls, 1):
                future = executor.submit(process_single_url, url, output_filename,
                                       index % MAX_THREADS, total_urls, index)
                future_to_url[future] = (url, index)

            # Process completed tasks
            for future in as_completed(future_to_url):
                url, index = future_to_url[future]
                try:
                    result = future.result()

                    if result['status'] == 'success':
                        processed_new += 1
                    elif result['status'] == 'skipped':
                        skipped_existing += 1
                    else:
                        errors += 1

                    # Progress update
                    completed = processed_new + skipped_existing + errors
                    print(f"📊 Progress: {completed}/{total_urls} | New: {processed_new} | Skipped: {skipped_existing} | Errors: {errors}")

                except Exception as e:
                    errors += 1
                    print(f"❌ Thread execution error for {url}: {str(e)}")

    except KeyboardInterrupt:
        print(f"\n⚠️  Script interrupted by user")
        print(f"✅ Progress saved: {processed_new} URLs processed and saved to {output_filename}")
    except Exception as e:
        print(f"\n❌ An error occurred: {str(e)}")
        print(f"✅ Progress saved: {processed_new} URLs processed and saved to {output_filename}")

    finally:
        # Final summary
        print(f"\n{'='*80}")
        print(f"MULTITHREADED EXTRACTION COMPLETED!")
        print(f"{'='*80}")
        print(f"Total URLs: {total_urls}")
        print(f"New URLs processed: {processed_new}")
        print(f"Already existing (skipped): {skipped_existing}")
        print(f"Errors encountered: {errors}")
        print(f"Output file: {output_filename}")
        print(f"Threads used: {MAX_THREADS}")

        # Count final records in output file
        try:
            if os.path.exists(output_filename):
                with open(output_filename, 'r', newline='', encoding='utf-8') as file:
                    reader = csv.DictReader(file)
                    final_count = sum(1 for _ in reader)
                print(f"Total records in output file: {final_count}")
            else:
                print(f"No output file created")
        except Exception as e:
            print(f"Warning: Could not count final records: {e}")

        print("="*80)

def create_chrome_driver(thread_id=0):
    """
    Create a Chrome driver with improved version compatibility and thread safety using standard Selenium
    """
    # Detect Chrome version for better compatibility
    chrome_version = get_chrome_version()
    if chrome_version:
        print(f"🔍 Detected Chrome version: {chrome_version}")

    def create_chrome_options(thread_id, suffix=""):
        """Create fresh Chrome options to avoid reuse errors"""
        options = ChromeOptions()
        options.add_argument('--window-size=1920,1080')
        #options.add_argument('--disable-blink-features=AutomationControlled')
        #options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--no-sandbox')
        #options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-extensions')
        #options.add_argument('--disable-plugins')
        #options.add_argument('--disable-images')
        #options.add_argument('--disable-javascript')
        # Use Windows-compatible temp directory path
        temp_profile = f"C:\\temp\\chrome_profile_{thread_id}_{suffix}_{int(time.time())}"
        options.add_argument(f'--user-data-dir={temp_profile}')
        return options

    driver = None

    # Method 1: Try with webdriver-manager for automatic version management
    if WEBDRIVER_MANAGER_AVAILABLE:
        try:
            print(f"🔄 [Thread {thread_id}] Trying webdriver-manager for automatic ChromeDriver management...")
            service = Service(ChromeDriverManager().install())
            options = create_chrome_options(thread_id, "webdriver_manager")
            driver = webdriver.Chrome(service=service, options=options)
            print(f"✅ [Thread {thread_id}] Successfully created Chrome driver using webdriver-manager")
            return driver
        except Exception as e:
            print(f"❌ [Thread {thread_id}] webdriver-manager failed: {e}")

    # Method 2: Try with system ChromeDriver (if available in PATH)
    if driver is None:
        try:
            print(f"🔄 [Thread {thread_id}] Trying system ChromeDriver...")
            options = create_chrome_options(thread_id, "system")
            driver = webdriver.Chrome(options=options)
            print(f"✅ [Thread {thread_id}] Successfully created Chrome driver using system ChromeDriver")
            return driver
        except Exception as e:
            print(f"❌ [Thread {thread_id}] System ChromeDriver failed: {e}")

    # Method 3: Try with explicit ChromeDriver service (fallback)
    if driver is None:
        try:
            print(f"🔄 [Thread {thread_id}] Trying explicit ChromeDriver service...")
            options = create_chrome_options(thread_id, "explicit")
            # Try to find ChromeDriver in common locations
            possible_paths = [
                "chromedriver.exe",
                "C:\\chromedriver\\chromedriver.exe",
                "C:\\Program Files\\Google\\Chrome\\Application\\chromedriver.exe",
                "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chromedriver.exe"
            ]

            for path in possible_paths:
                try:
                    if os.path.exists(path):
                        service = Service(path)
                        driver = webdriver.Chrome(service=service, options=options)
                        print(f"✅ [Thread {thread_id}] Successfully created Chrome driver using {path}")
                        return driver
                except Exception:
                    continue

            # If no specific path works, try without service
            driver = webdriver.Chrome(options=options)
            print(f"✅ [Thread {thread_id}] Successfully created Chrome driver with default service")
            return driver

        except Exception as e:
            print(f"❌ [Thread {thread_id}] Explicit ChromeDriver service failed: {e}")

    # If all methods failed
    if driver is None:
        print(f"\n🔧 [Thread {thread_id}] ALL METHODS FAILED - SOLUTION SUGGESTIONS:")
        print("1. Update Chrome browser: chrome://settings/help")
        print("2. Install/update webdriver-manager: pip install --upgrade webdriver-manager")
        print("3. Download ChromeDriver from: https://chromedriver.chromium.org/downloads")
        print("4. Ensure ChromeDriver is in your PATH or place chromedriver.exe in the script directory")
        raise Exception("Failed to create Chrome driver with all methods")

    return driver

def process_single_url(url, output_filename, thread_id, total_urls, current_index):
    """
    Process a single URL in a thread-safe manner
    """
    driver = None
    try:
        print(f"\n[Thread {thread_id}] [{current_index}/{total_urls}] Processing URL: {url}")

        # Check if this URL was already processed (thread-safe check)
        if check_url_already_processed(url, output_filename):
            print(f"[Thread {thread_id}] ⏭️  Skipping - already processed")
            return {'status': 'skipped', 'url': url}

        # Create driver for this thread
        driver = create_chrome_driver(thread_id)
        wait = WebDriverWait(driver, 15)

        # Extract data from the URL
        result = scrape_data(url, driver, wait)

        # Thread-safe CSV writing
        success = append_result_to_csv(result, output_filename, write_header=False)

        if success:
            print(f"[Thread {thread_id}] ✅ Extracted and saved: {result.get('Name', 'N/A')}")
            print(f"[Thread {thread_id}]    Address: {result.get('Address', 'N/A')[:50]}...")
            print(f"[Thread {thread_id}]    Phone: {result.get('Phone', 'N/A')}")
            print(f"[Thread {thread_id}]    Coordinates: {result.get('Latitude', 'N/A')}, {result.get('Longitude', 'N/A')}")
            return {'status': 'success', 'url': url, 'result': result}
        else:
            print(f"[Thread {thread_id}] ❌ Failed to save result to CSV")
            return {'status': 'csv_error', 'url': url}

    except Exception as e:
        print(f"[Thread {thread_id}] ❌ Error processing URL: {str(e)}")

        # Still try to save an error record with coordinates
        try:
            latitude, longitude = extract_coordinates_from_url(url)
            error_result = {
                'URL': url,
                'Name': 'Error',
                'Address': 'Error',
                'Website': 'Error',
                'Phone': 'Error',
                'Latitude': latitude,
                'Longitude': longitude
            }
            append_result_to_csv(error_result, output_filename, write_header=False)
        except:
            pass

        return {'status': 'error', 'url': url, 'error': str(e)}

    finally:
        # Clean up driver
        if driver:
            safe_driver_quit(driver)



if __name__ == "__main__":
    main()
    
    

    
