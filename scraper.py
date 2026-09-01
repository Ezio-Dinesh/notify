import os
import json
import time
import base64
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv

load_dotenv()

# Same constants as original
OUTPUT_JSON_BASE = "notices"
DOWNLOAD_DIR_BASE = "downloaded_files"

def solve_captcha(b64_image, api_key):
    # (unchanged from original)
    print(f"   ⏳ Sending screenshot to 2Captcha...")
    payload = {
        'method': 'base64',
        'key': api_key,
        'body': b64_image,
        'json': 1,
        'numeric': 1
    }
    res = requests.post('https://2captcha.com/in.php', data=payload).json()
    if res['status'] != 1:
        raise Exception(f"2Captcha submission error: {res['request']}")
    captcha_id = res['request']
    print(f"   ⏳ Submitted. Waiting for solution (ID: {captcha_id})...")
    for _ in range(30):
        time.sleep(5)
        poll = requests.get(
            f'https://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}&json=1'
        ).json()
        if poll['status'] == 1:
            return poll['request']
        elif poll['request'] != 'CAPCHA_NOT_READY':
            raise Exception(f"2Captcha error: {poll['request']}")
    raise Exception("Timeout waiting for captcha solution.")

def close_metadata_modal(page):
    # (unchanged)
    try:
        modal = page.locator('#caNumpopupV')
        if modal.is_visible():
            print("   🔍 Detected metadata modal. Closing it automatically...")
            page.locator('a[ng-click="cancelcallback()"]').click()
            page.wait_for_timeout(1000)
    except:
        pass

class GSTNoticesDownloader:
    def __init__(self, username, password, api_key, target_date=None, job_id=None):
        self.username = username
        self.password = password
        self.api_key = api_key
        self.target_date = target_date
        self.job_id = job_id or username  # fallback
        self.base_dir = os.path.join("data", self.job_id)
        self.download_dir = os.path.join(self.base_dir, DOWNLOAD_DIR_BASE)
        os.makedirs(self.download_dir, exist_ok=True)
        self.output_json = os.path.join(self.base_dir, f"{OUTPUT_JSON_BASE}.json")
        self.collected_files = []  # will store all downloaded file paths

    # ---- Original methods (extract_table_data, close_details_modal, etc.) ----
    # They remain exactly as in your script, but we'll adjust download_notices
    # to collect file paths and return them.

    def extract_table_data(self, page):
        # (unchanged)
        page.wait_for_selector('table', state='visible', timeout=15000)
        header_row = page.locator('table thead tr').first
        header_elements = header_row.locator('th').all()
        headers = []
        for h in header_elements:
            text = h.inner_text().strip()
            if text:
                headers.append(text)
        rows = []
        for row in page.locator('table tbody tr').all():
            cells = row.locator('td').all()
            if cells:
                row_data = [c.inner_text().strip() for c in cells]
                if len(row_data) >= len(headers):
                    rows.append(row_data[:len(headers)])
        return headers, rows

    # Inside class GSTNoticesDownloader, add this method:

    def verify_login(self) -> tuple:
        """
        Attempts to log in and verifies that AuthToken and EntityRefId cookies are present.
        Returns (True, "Login successful") or (False, "error message").
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                accept_downloads=True
            )
            page = context.new_page()

            try:
                print("🌐 Navigating to GST login...")
                page.goto('https://services.gst.gov.in/services/login', wait_until='domcontentloaded')
                page.wait_for_selector('form[name="loginform"]', state='attached', timeout=30000)

                print("⌨️ Entering Username and Password...")
                page.fill('#username', self.username)
                page.fill('#user_pass', self.password)

                print("⏳ Waiting for CAPTCHA to load...")
                page.wait_for_selector('div[data-captcha] img#imgCaptcha', state='visible', timeout=15000)
                time.sleep(1)

                # Solve CAPTCHA
                captcha_element = page.locator('div[data-captcha] img#imgCaptcha')
                img_bytes = captcha_element.screenshot(type='png')
                b64_image = base64.b64encode(img_bytes).decode('utf-8')
                captcha_text = solve_captcha(b64_image, self.api_key)
                print(f"   ✅ Solved: {captcha_text}")

                print("⌨️ Pasting CAPTCHA...")
                page.fill('#captcha', captcha_text)
                page.wait_for_timeout(2000)

                print("Clicking Login button...")
                page.click('button[type="submit"]')
                page.wait_for_load_state('networkidle', timeout=15000)

                # Check for success (presence of Services dropdown)
                try:
                    page.wait_for_selector('a.dropdown-toggle:has-text("Services")', state='visible', timeout=10000)
                    print("✅ Login Successful!")

                    # Close any metadata modal
                    close_metadata_modal(page)

                    # 🔥 IMPORTANT: Wait for cookies to be fully set (same as in original code)
                    print("⏳ Waiting 4 seconds for cookies to set...")
                    page.wait_for_timeout(4000)

                    # Now retrieve cookies
                    cookies = context.cookies('https://services.gst.gov.in')
                    auth_cookie = next((c for c in cookies if c['name'] == 'AuthToken'), None)
                    ref_cookie = next((c for c in cookies if c['name'] == 'EntityRefId'), None)

                    if auth_cookie and auth_cookie.get('value') and ref_cookie and ref_cookie.get('value'):
                        return True, "Login successful"
                    else:
                        missing = []
                        if not auth_cookie: missing.append("AuthToken")
                        if not ref_cookie: missing.append("EntityRefId")
                        return False, f"Cookies missing: {', '.join(missing)}"
                except:
                    # Check for error message
                    error_container = page.locator('div.err[data-ng-show="errors.login_error"]')
                    error_msg = error_container.inner_text().strip() if error_container.count() > 0 else "Unknown login error"
                    return False, f"Login failed: {error_msg}"

            except Exception as e:
                return False, f"Exception during login: {str(e)}"
            finally:
                browser.close()
    
    def download_notices(self, page):
        # Modified to collect file paths and return them.
        print("   ⬇️ Starting to download notices...")
        rows = page.locator('table tbody tr').all()
        downloaded_count = 0
        downloaded_files = []  # local list

        for i, row in enumerate(rows):
            if self.target_date:
                cells = row.locator('td').all()
                if len(cells) >= 5:
                    date_issue = cells[3].inner_text().strip()
                    due_date = cells[4].inner_text().strip()
                    if self.target_date not in (date_issue, due_date):
                        continue

            view_link = row.locator('a[ng-click*="clickView(detail)"], a[ng-click*="dwnldSuppDoc"], a:has-text("View")')
            if view_link.count() == 0:
                continue

            print(f"   🔍 Processing notice #{i+1}...")

            # 1) Try direct download
            try:
                with page.expect_download(timeout=10000) as download_info:
                    view_link.click()
                download = download_info.value
                file_name = f"direct_{i+1}_{download.suggested_filename}"
                file_path = os.path.join(self.download_dir, file_name)
                download.save_as(file_path)
                print(f"   ✅ Direct download saved: {file_path}")
                downloaded_count += 1
                downloaded_files.append(file_path)
                continue
            except PlaywrightTimeoutError:
                pass

            # 2) Details page navigation
            print(f"   🔍 Opening details page for notice #{i+1}...")
            try:
                page.wait_for_load_state('domcontentloaded', timeout=15000)
                page.wait_for_selector('.list-group', state='visible', timeout=10000)
                page.wait_for_selector('.col-md-10', state='visible', timeout=10000)
                self.process_notice_details(page, downloaded_files)  # pass list
                self.close_details_modal(page)
                page.wait_for_selector('table', state='visible', timeout=10000)
                downloaded_count += 1
                print(f"   ✅ Processed notice #{i+1}")
            except Exception as e:
                print(f"      ❌ Failed to process notice #{i+1}: {e}")

        print(f"   ✅ Successfully processed {downloaded_count} notice(s).")
        return downloaded_files

    def process_notice_details(self, page, file_list):
        # Modified to append to file_list instead of only printing
        tabs = page.locator('.list-group a.list-group-item:not(.ng-hide)').all()
        if not tabs:
            print("      ⚠️ No sidebar tabs found.")
            return
        for tab in tabs:
            tab_text = tab.inner_text().strip()
            print(f"      📂 Switching to tab: {tab_text}")
            tab.click()
            try:
                page.wait_for_selector('.col-md-10 table', state='visible', timeout=10000)
            except:
                print(f"      ⚠️ No table found for tab '{tab_text}'")
                continue
            page.wait_for_timeout(2000)
            table = page.locator('.col-md-10 table').first
            headers = table.locator('thead th').all()
            print(f"      📋 Headers found: {[h.inner_text().strip() for h in headers]}")
            attachment_col_index = -1
            for idx, th in enumerate(headers):
                if 'Attachments' in th.inner_text():
                    attachment_col_index = idx
                    break
            if attachment_col_index == -1:
                print(f"      ⚠️ Could not find 'Attachments' column in tab '{tab_text}'")
                continue
            print(f"      📌 'Attachments' column index: {attachment_col_index}")
            rows = table.locator('tbody tr').all()
            if not rows:
                print(f"      ⚠️ Table is empty for tab '{tab_text}'")
                continue
            for row_idx, row in enumerate(rows):
                cells = row.locator('td').all()
                if len(cells) <= attachment_col_index:
                    continue
                attachment_cell = cells[attachment_col_index]
                all_links = attachment_cell.locator('a').all()
                if all_links:
                    visible_links = [link for link in all_links if link.is_visible()]
                    if visible_links:
                        print(f"         📎 Found {len(visible_links)} visible attachment link(s) in row {row_idx+1}")
                        for link in visible_links:
                            try:
                                with page.expect_download() as download_info:
                                    link.click()
                                download = download_info.value
                                file_name = download.suggested_filename or f"attachment_{row_idx+1}.pdf"
                                file_path = os.path.join(self.download_dir, file_name)
                                download.save_as(file_path)
                                print(f"            ✅ Saved: {file_path}")
                                file_list.append(file_path)
                            except Exception as e:
                                print(f"            ❌ Download failed: {e}")
                    else:
                        print(f"         ⚠️ No visible attachment links found in row {row_idx+1}")
                else:
                    print(f"         ⚠️ No attachment links found in row {row_idx+1}")

    def close_details_modal(self, page):
        # (unchanged)
        close_selectors = [
            '.modal-header .close',
            '.modal-footer button[data-dismiss="modal"]',
            'button.close',
            'button:has-text("Close")',
            'a:has-text("Back")',
            'button:has-text("Cancel")',
            '.modal-footer button:has-text("Close")',
        ]
        close_found = False
        for selector in close_selectors:
            element = page.locator(selector)
            if element.count() > 0 and element.is_visible():
                print(f"   🔹 Clicking close button: {selector}")
                try:
                    element.click(timeout=5000)
                    close_found = True
                    break
                except:
                    continue
        if not close_found:
            print("   ⚠️ No close button found. Trying to go back via browser history.")
            page.go_back()

    def run_and_collect(self):
        """Main entry point: runs the scraper and returns (headers, rows, file_paths)."""
        with sync_playwright() as p:
            max_attempts = 3
            browser = None
            context = None
            page = None
            for attempt in range(1, max_attempts + 1):
                print(f"\n🔄 Account {self.username}: Attempt {attempt}/{max_attempts}")
                try:
                    if browser:
                        browser.close()
                    browser = p.chromium.launch(
                        headless=True,
                        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
                    )
                    context = browser.new_context(
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                        accept_downloads=True
                    )
                    page = context.new_page()

                    # ---- Login flow (unchanged) ----
                    print("🌐 Navigating to GST login...")
                    page.goto('https://services.gst.gov.in/services/login', wait_until='domcontentloaded')
                    page.wait_for_selector('form[name="loginform"]', state='attached', timeout=30000)

                    print("⌨️ Entering Username and Password...")
                    page.fill('#username', self.username)
                    page.fill('#user_pass', self.password)

                    print("⏳ Waiting for CAPTCHA to load...")
                    page.wait_for_selector('div[data-captcha] img#imgCaptcha', state='visible', timeout=15000)
                    time.sleep(1)

                    try:
                        captcha_element = page.locator('div[data-captcha] img#imgCaptcha')
                        captcha_element.screenshot(path=f'captcha_debug_{self.username}.png')
                        img_bytes = captcha_element.screenshot(type='png')
                        b64_image = base64.b64encode(img_bytes).decode('utf-8')
                        captcha_text = solve_captcha(b64_image, self.api_key)
                        print(f"   ✅ Solved: {captcha_text}")
                    except Exception as e:
                        print(f"   ❌ Captcha capture/solving failed: {e}")
                        raise e

                    print("⌨️ Pasting CAPTCHA...")
                    page.fill('#captcha', captcha_text)
                    print("⏳ Waiting 2 seconds for Angular binding...")
                    page.wait_for_timeout(2000)

                    print("Clicking Login button...")
                    page.click('button[type="submit"]')
                    page.wait_for_load_state('networkidle', timeout=15000)

                    try:
                        page.wait_for_selector('a.dropdown-toggle:has-text("Services")', state='visible', timeout=10000)
                        print("✅ Login Successful!")
                        close_metadata_modal(page)
                    except:
                        error_container = page.locator('div.err[data-ng-show="errors.login_error"]')
                        error_msg = error_container.inner_text().strip() if error_container.count() > 0 else "Unknown login error"
                        raise Exception(f"Login failed. Server says: '{error_msg}'")

                    print("\n⏳ Waiting 4 seconds for cookies to set...")
                    page.wait_for_timeout(4000)

                    cookies = context.cookies('https://services.gst.gov.in')
                    auth_cookie = next((c for c in cookies if c['name'] == 'AuthToken'), None)
                    ref_cookie = next((c for c in cookies if c['name'] == 'EntityRefId'), None)

                    if not (auth_cookie and auth_cookie.get('value') and ref_cookie and ref_cookie.get('value')):
                        missing = []
                        if not auth_cookie: missing.append("AuthToken")
                        if not ref_cookie: missing.append("EntityRefId")
                        raise Exception(f"Cookies missing: {', '.join(missing)}. Authentication failed.")

                    print("✅ AuthToken and EntityRefId cookies successfully verified!")

                    print("\n🧭 Redirecting to Notices page using JS...")
                    page.evaluate("window.location.href = '//services.gst.gov.in/services/auth/notices';")
                    print("⏳ Waiting for navigation to complete...")
                    page.wait_for_load_state('domcontentloaded', timeout=30000)
                    current_url = page.url

                    if "fowelcome" in current_url:
                        print("⚠️ Landed on welcome page. Attempting to proceed...")
                        proceed_selectors = [
                            'a:has-text("Proceed")', 'button:has-text("Proceed")',
                            'a:has-text("Continue")', 'button:has-text("Continue")',
                            'a:has-text("Next")', 'button:has-text("Next")',
                            'a:has-text("Go to Dashboard")', 'button:has-text("Go to Dashboard")'
                        ]
                        proceed_btn = None
                        for selector in proceed_selectors:
                            element = page.locator(selector)
                            if element.count() > 0 and element.is_visible():
                                proceed_btn = element
                                break

                        if proceed_btn:
                            print("   🔹 Clicking proceed button...")
                            with page.expect_navigation(wait_until='domcontentloaded', timeout=30000):
                                proceed_btn.click()
                            print("   ✅ Proceed clicked.")
                            page.wait_for_timeout(2000)
                            current_url = page.url
                        else:
                            print("   ⚠️ No proceed button found. Trying direct navigation to notices...")

                        if "notices" not in current_url:
                            print("   🔄 Redirecting to notices directly...")
                            page.evaluate("window.location.href = '//services.gst.gov.in/services/auth/notices';")
                            page.wait_for_load_state('domcontentloaded', timeout=30000)
                            current_url = page.url
                            print(f"   ✅ After redirect, URL: {current_url}")

                    print(f"✅ Final URL: {current_url}")

                    print("📄 Waiting for Notice Table to load...")
                    page.wait_for_selector('table', state='visible', timeout=20000)
                    close_metadata_modal(page)
                    print("📄 Notice Page Loaded successfully!")

                    # ---- Scrape and download ----
                    all_data = []
                    page_num = 1
                    headers = []

                    while True:
                        print(f"   📊 Scraping page {page_num}...")
                        current_headers, current_rows = self.extract_table_data(page)
                        if page_num == 1 and current_headers:
                            headers = current_headers
                            print(f"      Headers: {headers}")

                        if self.target_date is not None:
                            filtered_rows = []
                            for row in current_rows:
                                if len(row) >= 5:
                                    date_issue = row[3].strip()
                                    due_date = row[4].strip()
                                    if self.target_date in (date_issue, due_date):
                                        filtered_rows.append(row)
                            current_rows = filtered_rows
                            print(f"      Filtered to {len(current_rows)} rows matching date {self.target_date}")

                        all_data.extend(current_rows)

                        next_button = page.locator('li.next a, li.pagination-next a, a:has-text("Next"), button:has-text("Next")').first
                        if next_button.count() > 0 and not next_button.is_disabled():
                            print("      ➡️ Moving to next page...")
                            next_button.click()
                            page.wait_for_load_state('networkidle')
                            time.sleep(2)
                            page_num += 1
                        else:
                            print("      ✅ No more pages.")
                            break

                    # Download notices and collect file paths
                    downloaded_files = self.download_notices(page)

                    # Save JSON data
                    output = {"headers": headers, "data": all_data, "total_records": len(all_data)}
                    with open(self.output_json, 'w', encoding='utf-8') as f:
                        json.dump(output, f, indent=4)

                    print(f"\n🎉 Account {self.username}: All done!")
                    print(f"   📋 Notice data saved to '{self.output_json}'")
                    print(f"   📂 Notice PDFs saved to '{self.download_dir}/'")

                    # Return the results
                    return headers, all_data, downloaded_files

                except Exception as e:
                    print(f"❌ Attempt {attempt} failed: {e}")
                    if browser:
                        browser.close()
                    if attempt == max_attempts:
                        raise e
                    print("   🔄 Retrying login from scratch...")
                    continue