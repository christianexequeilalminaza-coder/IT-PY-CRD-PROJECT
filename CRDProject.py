from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import json
import time
import re
import html
import requests 

# --- Configuration ---
BASE_DOMAIN = "https://kmt.vander-lingen.nl"
ARCHIVE_URL = f"{BASE_DOMAIN}/archive"
JSON_OUTPUT_FILE = "kmt_CRDoutput.json"

# Global Variables for Driver and Session
driver = None
session = None

def init_driver():
    """Initializes a Visible Chrome Driver optimized for speed."""
    opts = Options()
    
    # === SPEED OPTIMIZATIONS ===
    opts.page_load_strategy = 'eager' 
    
    opts.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,          
        "profile.managed_default_content_settings.stylesheets": 2,     
        "profile.managed_default_content_settings.cookies": 2,         
        "profile.managed_default_content_settings.javascript": 1,      
        "profile.managed_default_content_settings.plugins": 2,
        "profile.managed_default_content_settings.popups": 2,
        "profile.managed_default_content_settings.geolocation": 2,
        "profile.managed_default_content_settings.media_stream": 2,
    })

    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--log-level=3") 
    opts.add_argument("--start-maximized")

    d = webdriver.Chrome(options=opts)
    d.implicitly_wait(2) 
    return d

def init_session():
    """Creates a requests session for fast background data fetching."""
    sess = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    sess.mount("https://", HTTPAdapter(max_retries=retries))
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    })
    return sess

def update_cookies():
    """Syncs cookies once per article to keep the session valid."""
    session.cookies.clear()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie['name'], cookie['value'])

def fetch_text_fast(url):
    """Fastest way to get text content."""
    try:
        return session.get(url, timeout=10).text
    except: return None

def extract_chemical_data(xml_string):
    """Parses XML string using Regex."""
    result = {'reaction_smiles': None, 'components': []}
    try:
        rxn_match = re.search(r'<reactionSmiles>(.*?)</reactionSmiles>', xml_string, re.DOTALL)
        if rxn_match: result['reaction_smiles'] = html.unescape(rxn_match.group(1).strip())

        for mol_block in re.findall(r'<molecule>(.*?)</molecule>', xml_string, re.DOTALL):
            def g(t, x): m = re.search(f'<{t}>(.*?)</{t}>', x, re.DOTALL); return html.unescape(m.group(1).strip()) if m else None
            result['components'].append({
                "role": g('role', mol_block),
                "name": g('name', mol_block),
                "structure": {"smiles": g('smiles', mol_block)}
            })
    except: pass
    return result

def fetch_reaction_list():
    """Fetches the list of reaction data items and handles user input for limit."""
    print(f"Connecting to Archive: {ARCHIVE_URL}")
    driver.get(ARCHIVE_URL)
    time.sleep(2) 

    reaction_data = []
    for link in driver.find_elements(By.TAG_NAME, "a"):
        if "reaction data" in link.text:
            reaction_data.append(link.get_attribute("href"))
    
    total_found = len(reaction_data)
    print(f"Found {total_found} total reaction data items available.")
    
    # --- USER INPUT LOGIC ---
    limit = 0
    while True:
        try:
            print("-"*60)
            user_input = input(f"How many reaction data items do you want to scrape? (Max: {total_found}): ")
            limit = int(user_input)
            if limit <= 0:
                print("Please enter a number greater than 0.")
            elif limit > total_found:
                print(f"Input exceeds available items. Limiting to {total_found}.")
                limit = total_found
                break
            else:
                break
        except ValueError:
            print("Invalid input. Please enter a number.")
    print("-"*60)
    print(f"Starting scrape for {limit} items...")
    return reaction_data[:limit]

def process_detail_page_fast(details_url):
    """Fetches data using Requests Session (High Speed)."""
    try:
        html_content = fetch_text_fast(details_url)
        if not html_content: return None
        
        xml_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*XML\s*</a>', html_content, re.IGNORECASE)
        
        if xml_match:
            raw_path = xml_match.group(1)
            full_xml_url = BASE_DOMAIN + raw_path if raw_path.startswith("/") else raw_path
            
            xml_data = fetch_text_fast(full_xml_url)
            if xml_data:
                parsed = extract_chemical_data(xml_data)
                return {
                    'url': details_url,
                    'reaction_smiles': parsed['reaction_smiles'],
                    'components': parsed['components']
                }
    except: return None
    return None

def crawl_reaction_pages(start_url, idx, total_reactions):
    """Crawls all pages of a single reaction data item."""
    scraped_records = []
    print("\n" + "="*80)
    print(f"PROCESSING REACTION DATA [{idx}/{total_reactions}]: {start_url}")
    print("="*80)
    
    driver.get(start_url)
    update_cookies() 
    page_num = 1
    
    while True:
        detail_links = []
        buttons = driver.find_elements(By.CSS_SELECTOR, "a.btn.btn-outline-info[id^='title-']")
        
        for btn in buttons:
            if "Details" in btn.text:
                detail_links.append(btn.get_attribute("href"))

        if not detail_links: break

        print(f"   [Page {page_num}] Fast-scanning {len(detail_links)} reactions...")

        for link in detail_links:
            data = process_detail_page_fast(link)
            if data:
                scraped_records.append(data)
                smiles_preview = data['reaction_smiles'][:50] + "..." if data['reaction_smiles'] else "N/A"
                print(f"    + Found: {smiles_preview}")

        try:
            next_links = [a for a in driver.find_elements(By.TAG_NAME, "a") if "Next" in a.text]
            if not next_links: break
            
            next_btn = next_links[-1]
            parent = next_btn.find_element(By.XPATH, "./..")
            if "disabled" in parent.get_attribute("class"): break

            driver.execute_script("arguments[0].click();", next_btn)
            page_num += 1
            time.sleep(0.5) 
        except:
            print("   [Info] Pagination ended.")
            break
    
    print(f"\n✅ Completed process for Reaction Data {idx}")
    return scraped_records

def main():
    global driver, session
    
    # Initialize
    driver = init_driver()
    session = init_session()
    
    print("Starting Scraper...")
    
    try:
        reaction_data = fetch_reaction_list()
        
        if not reaction_data:
            print("No reaction data found to scrape. Exiting.")
            return

        start_time = time.time()
        all_data = {}
        
        for i, url in enumerate(reaction_data, 1):
            all_data[url] = crawl_reaction_pages(url, i, len(reaction_data))
            
        with open(JSON_OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)
        
        duration = time.time() - start_time
        print("\n" + "#"*40)
        print(f"COMPLETED PROCESS: ALL TASKS FINISHED in {duration:.2f} seconds")
        print(f"Data successfully saved to: {JSON_OUTPUT_FILE}")
        print("#"*40)
        print("-" * 60)
        print(
            "Developed by:\n"
            "Belandres Gabriel Andrew S.\n"
            "Alminaza Christian Exequeil"
        )
        print("-" * 60)
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()