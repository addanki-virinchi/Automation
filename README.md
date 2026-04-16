# Automation scripts (Naukri / Google / Maps)

This folder is a small collection of standalone scripts (mostly Selenium-based) used for:
- Updating your **Naukri profile summary** from a rotating text file.
- Scraping **Naukri jobs** (recommended jobs and keyword search with pagination / recent jobs).
- Enriching company lists via **Google (AI Mode)** to find emails/websites/LinkedIn.
- Scraping **Google Maps** place details from a list of Maps URLs.

## The two files you were remembering

1) **Login to Naukri + update profile summary (from the summaries file)**  
   - Script: `naukri_profile_summary_updater.py`  
   - Input text: `profile_summaries.txt`

2) **Get jobs / recent jobs by date (uses `jobAge=3`)**  
   - Script: `naukri_jobs_keyword_search_scraper.py`  
   - Output CSV: `naukri_jobs_py.csv`

## Renamed files (for easier recognition)

These are the renames done in this folder:
- `naukri_profile_editor.py` → `naukri_profile_summary_updater.py`
- `Profile_Summeries.txt` → `profile_summaries.txt`
- `pagination.py` → `naukri_jobs_keyword_search_scraper.py`
- `naukri.py` → `naukri_jobs_recommended_scraper.py`
- `naukri_pagi.py` → `naukri_jobs_recommended_scraper_quick.py`
- `Hi.py` → `google_ai_mode_company_contact_scraper.py`
- `Improved.py` → `google_ai_mode_company_contact_scraper_incremental.py`
- `Extract_Maps.py` → `google_maps_place_details_scraper_threadpool.py`
- `X-maps.py` → `google_maps_place_details_scraper_workers.py`
- `test.py` → `selenium_smoke_test_naukri.py`

## Credentials / config (`.env`)

Scripts that need Naukri login read credentials from environment variables (and also load a local `.env` file).

Expected keys:
```env
NAUKRI_USERNAME="you@example.com"
NAUKRI_PASSWORD="your_password"
HEADLESS="true"
```

Notes:
- `.env` is ignored by git via `.gitignore`.
- `HEADLESS` is used by `naukri_profile_summary_updater.py`. The job scrapers currently run non-headless by default.

## File-by-file guide (what each file does + flow)

### Naukri: profile automation

- `naukri_profile_summary_updater.py`
  - What it does: Logs into Naukri and updates the **Profile Summary** field.
  - Inputs:
    - `profile_summaries.txt` (default) for the rotating summaries (Day-1 … Day-7).
    - `profile.html` (optional) used to parse/extract current summary text for debugging.
    - `.env` for `NAUKRI_USERNAME` / `NAUKRI_PASSWORD` and `HEADLESS`.
  - Flow:
    1. Load `.env` (if present).
    2. Pick a summary for the current weekday (IST).
    3. Open Chrome (Selenium) and log in if needed.
    4. Navigate to the profile page.
    5. Open the Profile Summary editor, paste the new summary, click Save/Update.
    6. Optional scheduling:
       - `--install-task` creates a Windows Task Scheduler task.
       - `--schedule-loop` runs a persistent loop and updates daily.

- `profile_summaries.txt`
  - What it does: Text source for profile summaries.
  - Format:
    - `Day-1: ...` through `Day-7: ...` (or plain lines; the script will auto-fill days if needed).

- `profile.html`
  - What it does: Saved HTML snapshot of a Naukri profile page (used for parsing/debugging).

### Naukri: job scraping

- `naukri_jobs_keyword_search_scraper.py`
  - What it does: Keyword-based Naukri search scraper with pagination, and detail-page extraction.
  - Outputs: Appends rows to `naukri_jobs_py.csv`.
  - Flow:
    1. Load `.env` (if present).
    2. Build Naukri search URLs for each keyword in `SEARCH_KEYWORDS`.
       - Uses `jobAge=3` in `SEARCH_QUERY` to keep results recent.
    3. For each search result page:
       - Collect job cards (title/company/location/experience/job id/url).
       - Open each job detail page and extract salary/skills/description/company info.
    4. Append each job row to CSV as it goes.
  - Useful env knobs:
    - `NAUKRI_MAX_PAGES` (default 5)
    - `NAUKRI_MAX_JOBS_PER_PAGE` (default 0 = no limit)
    - `NAUKRI_START_PAGE` (default 1)
    - `NAUKRI_DEBUG_HTML=1` to dump debug HTML files (see below)

- `naukri_jobs_recommended_scraper.py`
  - What it does: Scrapes the “python-development-jobs” listing URL and paginates via “Next”.
  - Output: Writes a full CSV at the end (`naukri_jobs.csv`).
  - Flow:
    1. Load `.env` (if present).
    2. Open the listing URL and log in if needed.
    3. Collect job cards, open each job’s detail page, extract fields.
    4. Click next page and repeat up to `MAX_PAGES`.
    5. Write all collected rows to CSV once finished.

- `naukri_jobs_recommended_scraper_quick.py`
  - What it does: Same as `naukri_jobs_recommended_scraper.py`, but tuned for fewer pages.
  - Output: `naukri_jobs_pagi.csv`
  - Typical use: quick runs / faster testing.

- `naukri_debug_card.html`, `naukri_debug_page.html`, `naukri_debug_detail.html`
  - What they do: Debug dumps written when `NAUKRI_DEBUG_HTML=1` is enabled.

- `selenium_smoke_test_naukri.py`
  - What it does: Minimal Selenium “smoke test” that opens a Naukri search URL.

### Google: company contact enrichment (AI Mode)

- `google_ai_mode_company_contact_scraper.py`
  - What it does: Reads a CSV (default `naukri_jobs_py.csv`), searches Google for each company, and extracts:
    - emails, website, and LinkedIn company page (best-effort).
  - Flow:
    1. Start undetected Chrome.
    2. For each company: open Google → search → optionally click “AI Mode”.
    3. Parse the page text for emails/links and write `results.csv`.

- `google_ai_mode_company_contact_scraper_incremental.py`
  - What it does: Same idea as above, but with:
    - incremental saving, de-dup, captcha detection, and an alert sound.
  - Related files:
    - `alert.mp3` (sound used for captcha alert)
    - `results.csv` (output)
    - `results_run.log` (run log)

### Google Maps: place details scraping

- `google_maps_place_details_scraper_workers.py`
  - What it does: Reads `company_urls.csv` (expects a `URL` column), scrapes details from each Google Maps URL, and writes `company_urls_op.csv`.
  - Flow:
    1. Load input URLs and load already-processed URLs from the output file.
    2. Run a small worker pool (each worker owns a persistent Chrome profile/driver).
    3. For each URL: scrape fields + coordinates and append to CSV.

- `google_maps_place_details_scraper_threadpool.py`
  - What it does: Similar Maps scraping, but uses a threadpool approach and multiple ChromeDriver creation fallbacks.

### LaTeX documents

- `ATS.tex`, `resume.tex`
  - What they do: LaTeX resume documents (not directly used by the scripts).

## Other files you’ll see

- `.env`
  - Local secrets/config (ignored by git). Used by the Naukri scripts.
- `.gitignore`
  - Currently ignores `.env`.
- `.git/`
  - Git metadata.
- `__pycache__/`
  - Python bytecode cache (auto-generated).
- `naukri_jobs_py.csv`
  - CSV output created by `naukri_jobs_keyword_search_scraper.py`.
- `results.csv`, `results_run.log`
  - Outputs created by `google_ai_mode_company_contact_scraper_incremental.py`.
