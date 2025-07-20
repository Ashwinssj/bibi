import os
import json
import uuid
import datetime
import time
import redis
from tavily import TavilyClient
import google.generativeai as genai
from serpapi import GoogleSearch
from exa_py import Exa
from openai import OpenAI
import re
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from pdfminer.high_level import extract_text
from django.conf import settings # Import Django settings

# Define a list of common academic/journal domains for focused search
ACADEMIC_DOMAINS = [
    "pubmed.ncbi.nlm.nih.gov", "sciencedirect.com", "springer.com",
    "elsevier.com", "wiley.com", "ieee.org", "acm.org", "mdpi.com",
    "nature.com", "science.org", "frontiersin.org", "plos.org",
    "bmj.com", "jamanetwork.com", "nejm.org", "arxiv.org", "biorxiv.org",
    "jstor.org", "cambridge.org", "oup.com", "tandfonline.com",
    "researchgate.net", "academia.edu", "doaj.org" # Added DOAJ to academic domains
]

# Corrected: DOAJ API Base URL for articles search
DOAJ_API_ARTICLES_URL = "https://doaj.org/api/v2/search/articles" # Global constant for articles
DOAJ_API_JOURNALS_URL = "https://doaj.org/api/v2/search/journals" # Global constant for journals

# Global clients - initialized once when the module is loaded
gemini_model = None
tavily_client = None
redis_client = None
serpapi_client = None
exa_client = None
openai_exa_client = None
SCRAPERAPI_API_KEY = None

def configure_clients():
    """Loads secrets and configures API clients and Redis connection."""
    global gemini_model, tavily_client, redis_client, serpapi_client, exa_client, openai_exa_client, SCRAPERAPI_API_KEY

    if gemini_model and tavily_client and redis_client and serpapi_client and exa_client and openai_exa_client and SCRAPERAPI_API_KEY:
        return # Already configured

    try:
        GOOGLE_API_KEY = settings.GOOGLE_API_KEY
        TAVILY_API_KEY = settings.TAVILY_API_KEY
        UPSTASH_REDIS_HOST = settings.UPSTASH_REDIS_HOST
        UPSTASH_REDIS_PORT = settings.UPSTASH_REDIS_PORT
        UPSTASH_REDIS_PASSWORD = settings.UPSTASH_REDIS_PASSWORD
        REDIS_URL = settings.REDIS_URL # New: For Render's managed Redis
        SERPAPI_API_KEY = settings.SERPAPI_API_KEY
        EXA_API_KEY = settings.EXA_API_KEY
        SCRAPERAPI_API_KEY = settings.SCRAPERAPI_API_KEY

        missing_vars = []
        if not GOOGLE_API_KEY: missing_vars.append("GOOGLE_API_KEY")
        if not TAVILY_API_KEY: missing_vars.append("TAVILY_API_KEY")
        # Check for Redis variables based on whether REDIS_URL is available
        if not REDIS_URL:
            if not UPSTASH_REDIS_HOST: missing_vars.append("UPSTASH_REDIS_HOST")
            if not UPSTASH_REDIS_PORT: missing_vars.append("UPSTASH_REDIS_PORT")
            if not UPSTASH_REDIS_PASSWORD: missing_vars.append("UPSTASH_REDIS_PASSWORD")
        if not SERPAPI_API_KEY: missing_vars.append("SERPAPI_API_KEY")
        if not EXA_API_KEY: missing_vars.append("EXA_API_KEY")
        if not SCRAPERAPI_API_KEY: missing_vars.append("SCRAPERAPI_API_KEY")
        
        print(f"DEBUG: Missing environment variables check: {missing_vars}")

        if missing_vars:
            raise ValueError(f"⚠️ Critical API keys or Redis connection details missing FROM ENV VARS: {', '.join(missing_vars)}. Please check Django settings and .env file.")

        # Configure Gemini
        genai.configure(api_key=GOOGLE_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')

        # Configure Tavily
        tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

        # Configure SerpApi
        serpapi_client = GoogleSearch

        # Configure Exa (for individual article search)
        exa_client = Exa(api_key=EXA_API_KEY)

        # Configure OpenAI client for Exa Research API
        openai_exa_client = OpenAI(
            base_url="https://api.exa.ai",
            api_key=EXA_API_KEY,
        )

        # Configure Redis
        if REDIS_URL:
            # Use REDIS_URL provided by Render's managed Redis
            print(f"DEBUG: Using REDIS_URL from Render: {REDIS_URL[:20]}...") # Mask URL for security
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        else:
            # Fallback to individual UPSTASH variables for local development or other setups
            if not isinstance(UPSTASH_REDIS_PORT, int) or UPSTASH_REDIS_PORT == 0:
                raise ValueError(f"🚨 Invalid or missing UPSTASH_REDIS_PORT: '{UPSTASH_REDIS_PORT}'. It must be a number.")

            print(f"DEBUG: Falling back to UPSTASH variables. Host: {UPSTASH_REDIS_HOST}, Port: {UPSTASH_REDIS_PORT}")
            redis_client = redis.Redis(
                host=UPSTASH_REDIS_HOST,
                port=UPSTASH_REDIS_PORT,
                password=UPSTASH_REDIS_PASSWORD,
                ssl=True, # Assuming Upstash always uses SSL
                decode_responses=True
            )
        print("DEBUG: Attempting Redis ping...")
        redis_client.ping()
        print("DEBUG: Redis ping successful.")

    except redis.exceptions.ConnectionError as e:
        raise ConnectionError(f"🚨 Could not connect to Redis: {e}. Please verify connection details and Upstash instance status.") from e
    except ValueError as e:
        raise ValueError(f"🚨 Configuration error: {e}") from e
    except Exception as e:
        raise Exception(f"🚨 An unexpected error occurred during configuration: {e}") from e

# Initialize clients on module load
configure_clients()

# --- Redis Data Structure Keys ---
FOLDER_MASTER_LIST_KEY = "folders"
FOLDER_PREFIX = "folder:"
FOLDER_ITEMS_PREFIX = "folder_items:"
ITEM_PREFIX = "item:"
CHAT_HISTORY_KEY_PREFIX = "chat_history:"

# --- Redis Helper Functions ---

# Folders
def create_folder(name):
    """Creates a new folder in Redis."""
    if not name or name.isspace():
        return None, "Folder name cannot be empty."
    folder_id = str(uuid.uuid4())
    folder_key = f"{FOLDER_PREFIX}{folder_id}"
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with redis_client.pipeline() as pipe:
            pipe.hset(folder_key, mapping={
                "id": folder_id,
                "name": name,
                "created_timestamp": timestamp
            })
            pipe.sadd(FOLDER_MASTER_LIST_KEY, folder_key)
            pipe.execute()
        return folder_id, None
    except Exception as e:
        return None, f"Error creating folder: {e}"

def get_folders():
    """Retrieves all folders from Redis."""
    try:
        folder_keys = list(redis_client.smembers(FOLDER_MASTER_LIST_KEY))
        folders = []
        if folder_keys:
            pipe = redis_client.pipeline()
            for key in folder_keys:
                pipe.hgetall(key)
            results = pipe.execute()
            for i, data in enumerate(results):
                 if data and 'name' in data and 'id' in data:
                     data['folder_key'] = folder_keys[i]
                     folders.append(data)
                 else:
                     # Inconsistent data, remove the key from the master list
                     redis_client.srem(FOLDER_MASTER_LIST_KEY, folder_keys[i])
            folders.sort(key=lambda x: x.get('name', '').lower())
        return folders, None
    except Exception as e:
        return [], f"Error retrieving folders: {e}"

def delete_folder(folder_key):
    """Deletes a folder metadata and its associated items set."""
    folder_id = folder_key.split(":", 1)[1]
    folder_items_key = f"{FOLDER_ITEMS_PREFIX}{folder_id}"
    try:
        with redis_client.pipeline() as pipe:
            pipe.delete(folder_key)
            pipe.delete(folder_items_key)
            pipe.srem(FOLDER_MASTER_LIST_KEY, folder_key)
            pipe.execute()
        return None
    except Exception as e:
        return f"Error deleting folder: {e}"

# Library Items
def save_library_item(title, url, query, source_type, folder_id="root", summary="", annotation="", content_snippet="", authors="", year="", pdf_url="", main_pub_url="", doi="", journal_name="", volume="", pages="", publisher="", issn=""):
    """Saves a library item to Redis."""
    item_uuid = str(uuid.uuid4())
    item_key = f"{ITEM_PREFIX}{item_uuid}"
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    item_data = {
        "id": item_uuid,
        "title": title or "Untitled",
        "url": url,
        "query": query or "",
        "summary": summary or "",
        "annotation": annotation or "",
        "content_snippet": content_snippet or "",
        "folder_id": folder_id if folder_id != "root" else "",
        "added_timestamp": timestamp,
        "source_type": source_type or "Website",
        "authors": authors or "",
        "year": str(year) if year else "",
        "pdf_url": pdf_url or "",
        "main_pub_url": main_pub_url or "",
        "doi": doi or "",
        "journal_name": journal_name or "",
        "volume": volume or "",
        "pages": pages or "",
        "publisher": publisher or "", # NEW
        "issn": issn or "" # NEW
    }
    try:
        with redis_client.pipeline() as pipe:
            pipe.hset(item_key, mapping=item_data)
            if folder_id != "root":
                folder_items_key = f"{FOLDER_ITEMS_PREFIX}{folder_id}"
                pipe.sadd(folder_items_key, item_key)
            pipe.execute()
        return item_key, None
    except Exception as e:
        return None, f"Error saving item: {e}"

def get_library_items(folder_id="root"):
    """Retrieves items, optionally filtered by folder_id."""
    try:
        item_keys = []
        if folder_id == "root":
            all_keys = redis_client.keys(f"{ITEM_PREFIX}*")
            if not all_keys: return [], None
            pipe = redis_client.pipeline()
            for key in all_keys:
                 pipe.hget(key, "folder_id")
            folder_ids = pipe.execute()
            item_keys = [key for key, f_id in zip(all_keys, folder_ids) if not f_id]
        else:
            folder_items_key = f"{FOLDER_ITEMS_PREFIX}{folder_id}"
            item_keys = list(redis_client.smembers(folder_items_key))

        items = []
        if item_keys:
            pipe = redis_client.pipeline()
            for key in item_keys:
                pipe.hgetall(key)
            results = pipe.execute()
            for i, data in enumerate(results):
                 if data:
                    data['item_key'] = item_keys[i]
                    items.append(data)
                 else:
                    # Inconsistent data, remove the key from the folder set
                    if folder_id != "root":
                        redis_client.srem(f"{FOLDER_ITEMS_PREFIX}{folder_id}", item_keys[i])

            items.sort(key=lambda x: x.get('added_timestamp', ''), reverse=True)
        return items, None
    except Exception as e:
        return [], f"Error retrieving library items: {e}"

def delete_library_item(item_key):
    """Deletes an item from Redis and removes it from its folder set."""
    try:
        folder_id = redis_client.hget(item_key, "folder_id")

        with redis_client.pipeline() as pipe:
            pipe.delete(item_key)
            if folder_id:
                folder_items_key = f"{FOLDER_ITEMS_PREFIX}{folder_id}"
                pipe.srem(folder_items_key, item_key)
            pipe.execute()
        return None
    except Exception as e:
        return f"Error deleting item: {e}"

# Chat History
def save_chat_message(session_id: str, role: str, content: str):
    """Appends a chat message to the Redis list for the session."""
    key = f"{CHAT_HISTORY_KEY_PREFIX}{session_id}"
    message = json.dumps({"role": role, "content": content})
    try:
        redis_client.rpush(key, message)
        redis_client.ltrim(key, -100, -1) # Keep last 100 messages
        return None
    except Exception as e:
        return f"Could not save chat message to Redis: {e}"
    
def load_chat_history(session_id):
    """Loads chat history for the session from Redis."""
    key = f"{CHAT_HISTORY_KEY_PREFIX}{session_id}"
    try:
        message_strings = redis_client.lrange(key, 0, -1)
        return [json.loads(msg) for msg in message_strings], None
    except Exception as e:
        return [], f"Could not load chat history from Redis: {e}"

# --- API Call Helper Functions ---

def scrape_article_content(url):
    """
    Attempts to scrape the full text content from a given URL using ScraperAPI.
    Handles both HTML and PDF links.
    Returns the scraped text and an error message (or None).
    """
    if not url:
        return None, "No URL provided."

    # Base ScraperAPI URL
    scraperapi_url = "http://api.scraperapi.com/"
    
    # Headers to pass to ScraperAPI (ScraperAPI handles User-Agent rotation itself)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/555.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/555.36'
    }

    # Determine if it's likely a PDF to avoid unnecessary JS rendering
    is_pdf_url = ".pdf" in url.lower()

    # Parameters for ScraperAPI request
    params = {
        'api_key': SCRAPERAPI_API_KEY,
        'url': url,
    }
    if not is_pdf_url:
        # Enable JavaScript rendering for HTML pages, unless it's likely a PDF
        params['render'] = 'true' 

    try:
        response = requests.get(scraperapi_url, params=params, headers=headers, timeout=30) # Increased timeout for ScraperAPI
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '').lower()

        if 'application/pdf' in content_type:
            try:
                pdf_file = BytesIO(response.content)
                text = extract_text(pdf_file)
                text = re.sub(r'\s+', ' ', text).strip()
                return text, None
            except Exception as e:
                return None, f"Failed to extract text from PDF returned by ScraperAPI: {e}"
        elif 'text/html' in content_type:
            soup = BeautifulSoup(response.content, 'html.parser')

            main_content = None
            for tag_name in ['article', 'main', 'div']:
                main_content = soup.find(tag_name, class_=re.compile(r'(article|content|main|body|post|entry)', re.I))
                if main_content:
                    break
            
            if not main_content:
                main_content = soup.find('body')

            if main_content:
                for unwanted_tag in main_content(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'img', 'svg', 'canvas', 'noscript']):
                    unwanted_tag.decompose()
                
                text = main_content.get_text(separator=' ', strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                text = re.sub(r'(Skip to content|Privacy Policy|Terms of Use|Cookie Policy|All Rights Reserved|Copyright © \d{4}.*?|Read more|Continue reading)', '', text, flags=re.IGNORECASE).strip()
                
                if len(text) < 200:
                    return None, "Scraped HTML content was too short or seemed to lack main article text."
                
                return text, None
            else:
                return None, "Could not identify main article content on the page."
        else:
            return None, f"ScraperAPI returned unsupported content type: {content_type}"

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            return None, f"Access Forbidden (403): Likely a paywall or anti-scraping measure. Cannot scrape full content even with ScraperAPI."
        return None, f"HTTP error {e.response.status_code} during ScraperAPI request: {e}"
    except requests.exceptions.ConnectionError as e:
        return None, f"Connection error during ScraperAPI request: {e}"
    except requests.exceptions.Timeout as e:
        return None, f"Timeout during ScraperAPI request: {e}"
    except Exception as e:
        return None, f"An unexpected error occurred during ScraperAPI request: {e}"


def search_tavily(query, search_depth="basic", max_results=7):
    """Performs a search using the Tavily API, now with domain filtering."""
    try:
        response = tavily_client.search(
            query=query,
            search_depth=search_depth,
            max_results=max_results,
            include_domains=ACADEMIC_DOMAINS # NEW: Apply domain filter
        )
        return response.get('results', []), None
    except Exception as e:
        return [], str(e)

def optimize_scholar_query(user_query):
    """Uses Gemini to optimize a user's natural language query into a concise, effective,
    and keyword-rich search query suitable for academic databases like Google Scholar.
    The output query will be a single string of keywords, without internal double quotes.
    """
    prompt_template = f"""
    You are an AI research assistant. Your task is to rephrase a user's natural language research query into a concise, effective, and keyword-rich search query suitable for academic databases like Google Scholar.

    Focus on:
    - Extracting the core topic.
    - Expanding abbreviations (e.g., "AI" to "Artificial Intelligence").
    - Removing conversational filler ("I want to research...", "articles about...").
    - Prioritizing academic terms.
    - **Crucially, the output should be a space-separated list of keywords. Do NOT enclose any individual phrases or keywords in double quotes in your output.** Do NOT use commas to separate keywords in the output query.

    Example 1:
    User Query: "i want to research Articles in AI"
    Optimized Query: Artificial Intelligence research articles

    Example 2:
    User Query: "papers on climate change impact on agriculture"
    Optimized Query: climate change agriculture impact papers

    Example 3:
    User Query: "Machine learning with MCP model context protocol"
    Optimized Query: Machine Learning MCP model context protocol

    User Query: "{user_query}"
    Optimized Query:
    """
    optimized_query, error = generate_gemini(prompt_template)
    if error:
        return user_query, error
    
    optimized_query = optimized_query.strip()
    # Remove any double quotes that the model might still generate despite instructions
    optimized_query = optimized_query.replace('"', '')
    optimized_query = optimized_query.replace(',', ' ').strip()
    optimized_query = re.sub(r'\s+', ' ', optimized_query)

    return optimized_query, None

def optimize_doaj_query(user_query):
    """Uses Gemini to optimize a user's natural language query into a concise,
    space-separated list of keywords suitable for DOAJ API search.
    """
    prompt_template = f"""
    You are an AI research assistant. Your task is to rephrase a user's natural language research query into a concise, space-separated list of keywords suitable for searching academic databases like DOAJ.

    Focus on:
    - Extracting the core topic and key concepts.
    - Expanding common abbreviations (e.g., "AI" to "Artificial Intelligence", "EHR" to "Electronic Health Record").
    - Removing conversational filler ("I want to research...", "articles about...").
    - Prioritizing academic terms.
    - **Crucially, the output should be a space-separated list of keywords. Do NOT use double quotes around individual phrases or the entire output. Do NOT add any commas or other punctuation between keywords.**

    Example 1:
    User Query: "i want AI-Enhanced EHR System with Intelligent Prescription Processing and Automated Patient Engagement"
    Optimized Query: Artificial Intelligence Electronic Health Record Intelligent Prescription Processing Automated Patient Engagement

    Example 2:
    User Query: "papers on climate change impact on agriculture"
    Optimized Query: climate change agriculture impact

    Example 3:
    User Query: "Machine learning with MCP model context protocol"
    Optimized Query: Machine Learning MCP model context protocol

    User Query: "{user_query}"
    Optimized Query:
    """
    optimized_query, error = generate_gemini(prompt_template)
    if error:
        return user_query, error
    
    optimized_query = optimized_query.strip()
    # Remove any stray double quotes or commas
    optimized_query = optimized_query.replace('"', '').replace(',', '')
    # Ensure it's space-separated, consolidating multiple spaces
    optimized_query = re.sub(r'\s+', ' ', optimized_query).strip()

    return optimized_query, None


def search_google_scholar(query, num_results=7):
    """Performs a search using the SerpApi Google Scholar API with improved debugging."""
    scholar_results = []
    error = None
    
    params = {
        "engine": "google_scholar",
        "q": query,
        "num": num_results,
        "api_key": settings.SERPAPI_API_KEY
    }

    try:
        search = serpapi_client(params)
        results_json = search.get_dict()
        
        if results_json.get('search_metadata', {}).get('status') == 'Error':
            error = results_json.get('search_metadata', {}).get('error') or "Unknown SerpApi error."
            return [], error
        
        if 'error' in results_json:
            error = results_json['error']
            return [], error

        search_metadata = results_json.get('search_metadata', {}) 
        if search_metadata.get('status') != 'Success':
            error = f"Search not successful. Status: {search_metadata.get('status')}"
            return [], error

        organic_results = results_json.get('organic_results', [])

        if not organic_results:
            return [], None

        for i, result in enumerate(organic_results):
            
            title = result.get('title', 'No Title')
            snippet = result.get('snippet', 'No snippet available.')
            main_link = result.get('link')
            
            pdf_link = None
            source_type = "Google Scholar Article"
            doi = ""
            journal_name = ""
            authors = ""
            year = ""
            volume = ""
            pages = ""

            if 'resources' in result:
                for resource in result['resources']:
                    if resource.get('file_format') == 'PDF' and resource.get('link'):
                        pdf_link = resource['link']
                        break

            if snippet:
                doi_match = re.search(r'(10\.\d{4,}\/[^\s]+)', snippet)
                if doi_match:
                    doi = doi_match.group(1).strip()
            if not doi and main_link:
                doi_match = re.search(r'(10\.\d{4,}\/[^\s]+)', main_link)
                if doi_match:
                    doi = doi_match.group(1).strip()

            pub_summary = result.get('publication_info', {}).get('summary', '')
            if pub_summary:
                # Extract authors (text before the first '-' or before a year if no '-')
                author_match = re.match(r'^(.*?)(?: - |\b\d{4}\b|$)', pub_summary)
                if author_match:
                    authors = author_match.group(1).strip()
                
                # Extract year (e.g., "2017")
                year_match = re.search(r'\b(\d{4})\b', pub_summary)
                if year_match:
                    year = year_match.group(1)
                
                # Try to extract volume and pages from the summary (e.g., "69, S36-S40" or "541(7635), 1-10")
                # Pattern: (volume)(optional sub-volume)(optional comma/space/colon)(pages)
                # This regex aims to capture:
                # 1. Volume (e.g., 69 or 135)
                # 2. Optional sub-volume (e.g., (1))
                # 3. Pages (e.g., S36-S40 or 1-10)
                
                # Remove author part if present to simplify parsing the rest
                temp_summary_for_parsing = pub_summary
                if authors:
                    temp_summary_for_parsing = temp_summary_for_parsing.replace(authors, '', 1).strip(' -').strip(',')
                
                vol_pages_match = re.search(r'(\d+)(?:\((\d+)\))?(?:,\s*|:\s*|\s*)(S?\d+-\d+|\d+-\d+)', temp_summary_for_parsing)
                if vol_pages_match:
                    volume = vol_pages_match.group(1)
                    pages = vol_pages_match.group(3) # Group 3 captures the pages (e.g., S36-S40 or 1-10)
                else: # Try just volume if no pages
                    vol_match = re.search(r'\b(\d+)\b', temp_summary_for_parsing)
                    if vol_match and vol_match.group(1) != year: # Ensure it's not the year itself
                        volume = vol_match.group(1)

                # Extract journal name:
                # It's usually the part between authors and the year/volume/pages info.
                journal_candidate = temp_summary_for_parsing
                journal_candidate = re.sub(r'\b\d{4}\b', '', journal_candidate).strip(',').strip() # Remove year
                if volume:
                    journal_candidate = journal_candidate.replace(volume, '', 1).strip(',').strip()
                if pages:
                    journal_candidate = journal_candidate.replace(pages, '', 1).strip(',').strip()
                
                # Remove any remaining numeric or short parts, and common delimiters
                journal_candidate = re.sub(r'\b\d+\b', '', journal_candidate).strip() # Remove any remaining numbers
                journal_candidate = re.sub(r'[,;:\-()]', '', journal_candidate).strip() # Remove common delimiters

                if journal_candidate and len(journal_candidate) > 3: # Heuristic for journal name
                    journal_name = journal_candidate.split(',')[0].strip() # Take first part if comma separated

            url_to_use = main_link or pdf_link

            if url_to_use:
                scholar_results.append({
                    "title": title,
                    "url": url_to_use,
                    "pdf_url": pdf_link,
                    "main_pub_url": main_link,
                    "content_snippet": snippet,
                    "source_type": source_type,
                    "authors": authors,
                    "year": year,
                    "doi": doi,
                    "journal_name": journal_name,
                    "volume": volume,
                    "pages": pages
                })
            # else:
            #     # In Django, we won't have st.warning, but can log or add to error list
            #     pass

    except Exception as e:
        error = f"SerpApi Google Scholar search failed: {e}"
    
    return scholar_results, error

def search_exa(query, num_results=7):
    """Performs a search using the Exa.ai API (document retrieval), now with domain filtering."""
    exa_results = []
    error = None
    try:
        response = exa_client.search(
            query=query,
            num_results=num_results,
            type="neural",
            include_domains=ACADEMIC_DOMAINS # NEW: Apply domain filter
        )
        
        if response.results:
            for i, result in enumerate(response.results):
                title = result.title or "No Title"
                url = result.url
                snippet = result.text or "No snippet available."
                authors = result.author or ""
                year = ""
                if result.published_date:
                    try:
                        year = str(datetime.datetime.strptime(result.published_date, '%Y-%m-%d').year)
                    except ValueError:
                        year = result.published_date.split('-')[0]
                
                # Exa results typically don't provide volume/pages directly, so leave them empty
                volume = ""
                pages = ""

                if url:
                    exa_results.append({
                        "title": title,
                        "url": url,
                        "content_snippet": snippet,
                        "source_type": "Exa.ai Search",
                        "query": query,
                        "authors": authors,
                        "year": year,
                        "pdf_url": "", # Exa doesn't typically provide direct PDF links, but its main URL could be a PDF
                        "main_pub_url": url,
                        "doi": "",
                        "journal_name": "",
                        "volume": volume,
                        "pages": pages
                    })
                # else:
                #     pass # Log or handle missing URL

    except Exception as e:
        error = f"Exa.ai search failed: {e}"
    
    return exa_results, error

# Corrected DOAJ Search Function
def search_doaj(query, num_results=7):
    """Performs an article search using the DOAJ API, passing query as 'q' parameter."""
    doaj_results = []
    error = None
    try:
        # URL-encode the query to handle spaces and special characters
        encoded_query = requests.utils.quote(query)
        
        # Construct the URL with the query as a path parameter
        full_url = f"{DOAJ_API_ARTICLES_URL}/{encoded_query}"
        
        # Parameters dictionary should contain query parameters like page and pageSize
        params = {
            "page": 1, # Start from the first page as per curl example
            "pageSize": num_results
        }
        
        response = requests.get(full_url, params=params, timeout=15)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        
        data = response.json()
        
        if data.get('results'):
            for result in data['results']:
                bibjson = result.get('bibjson', {})
                
                title = bibjson.get('title', 'No Title')
                
                # Try to find a fulltext HTML link, then PDF, then any link
                url = None # This will be the primary URL for the item
                pdf_url = None
                main_pub_url = None

                links = bibjson.get('link', [])
                for link in links:
                    if link.get('type') == 'fulltext' and link.get('url'):
                        url = link['url']
                        main_pub_url = link['url'] # Assume fulltext is main publication URL
                    if link.get('type') == 'pdf' and link.get('url'):
                        pdf_url = link['url']
                
                # If no specific fulltext or PDF URL found, take the first available link
                if not url and links:
                    for link in links: # Iterate to find the first general URL
                        if link.get('url'):
                            url = link['url']
                            if not main_pub_url: # If main_pub_url wasn't set by fulltext, use this one
                                main_pub_url = url
                            break # Take the first available URL
                
                authors_list = bibjson.get('author', [])
                authors = ", ".join([a.get('name', '') for a in authors_list if a.get('name')])
                
                # Year can be at bibjson level or inside journal info
                year = bibjson.get('year', '')
                journal_info = bibjson.get('journal', {})
                journal_name = journal_info.get('title', '')
                volume = journal_info.get('volume', '')

                # Pages from start_page and end_page
                pages = ""
                start_page = bibjson.get('start_page')
                end_page = bibjson.get('end_page')

                if start_page is not None and end_page is not None:
                    pages = f"{start_page}-{end_page}"
                elif start_page is not None:
                    pages = str(start_page)
                elif end_page is not None:
                    pages = str(end_page)
                
                # Ensure year is string
                year = str(year) if year else ""
                
                doi_identifiers = [i.get('id') for i in bibjson.get('identifier', []) if i.get('type') == 'doi']
                doi = doi_identifiers[0] if doi_identifiers else ''

                # DOAJ search results typically don't include snippets or abstracts directly.
                # We can try to get the abstract from the bibjson if present, though it's rare in search results.
                content_snippet = bibjson.get('abstract', 'No abstract available from DOAJ search result.')
                if not content_snippet or len(content_snippet) < 50:
                    content_snippet = 'No snippet available from DOAJ search result.'


                if url:
                    doaj_results.append({
                        "title": title,
                        "url": url,
                        "pdf_url": pdf_url,
                        "main_pub_url": main_pub_url,
                        "content_snippet": content_snippet,
                        "source_type": "DOAJ Article",
                        "query": query,
                        "authors": authors,
                        "year": year,
                        "doi": doi,
                        "journal_name": journal_name,
                        "volume": volume,
                        "pages": pages
                    })
                # else:
                #     pass # Log or handle missing URL
        # else:
        #     pass # Log no articles found

    except requests.exceptions.RequestException as e:
        error = f"DOAJ API request failed: {e}"
    except json.JSONDecodeError:
        error = "Failed to parse JSON response from DOAJ API."
    except Exception as e:
        error = f"An unexpected error occurred during DOAJ search: {e}"
    
    return doaj_results, error

def search_doaj_journals(query, num_results=7):
    """Performs a journal search using the DOAJ API."""
    doaj_journal_results = []
    error = None
    try:
        # URL-encode the query to handle spaces and special characters
        encoded_query = requests.utils.quote(query)
        
        # Construct the URL with the query as a path parameter
        full_url = f"{DOAJ_API_JOURNALS_URL}/{encoded_query}"
        params = {
            "page": 1, # Start from the first page as per curl example
            "pageSize": num_results
        }
        
        response = requests.get(full_url, params=params, timeout=15)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        
        data = response.json()
        
        if data.get('results'):
            for result in data['results']:
                bibjson = result.get('bibjson', {})
                
                title = bibjson.get('title', 'No Title')
                publisher = bibjson.get('publisher', '')
                
                issns = []
                for identifier in bibjson.get('identifier', []):
                    if identifier.get('type') in ['pissn', 'eissn'] and identifier.get('id'):
                        issns.append(identifier['id'])
                issn = ", ".join(issns) if issns else ""

                journal_url = None
                links = bibjson.get('link', [])
                # First, try to find a homepage link
                for link in links:
                    if link.get('type') == 'homepage' and link.get('url'):
                        journal_url = link['url']
                        break
                # If no homepage found, try to find any link with a URL
                if not journal_url:
                    for link in links:
                        if link.get('url'):
                            journal_url = link['url']
                            break # Take the first available URL
                
                # Content snippet for a journal could be its keywords or a general description
                keywords = bibjson.get('keywords', [])
                content_snippet = f"Keywords: {', '.join(keywords)}" if keywords else "No detailed description available for this journal."

                if journal_url:
                    doaj_journal_results.append({
                        "title": title,
                        "url": journal_url,
                        "pdf_url": "", # Not applicable for journal entry
                        "main_pub_url": journal_url,
                        "content_snippet": content_snippet,
                        "source_type": "DOAJ Journal", # Specific source type for journals
                        "query": query,
                        "authors": "", # Not applicable for journal entry
                        "year": "", # Not applicable for journal entry
                        "doi": "", # Not applicable for journal entry
                        "journal_name": title, # Journal title is the title of the entry
                        "volume": "", # Not applicable for journal entry
                        "pages": "", # Not applicable for journal entry
                        "publisher": publisher, # New field for journals
                        "issn": issn # New field for journals
                    })
                # else:
                #     pass # Log or handle missing URL
        # else:
        #     pass # Log no journals found

    except requests.exceptions.RequestException as e:
        error = f"DOAJ Journal API request failed: {e}"
    except json.JSONDecodeError:
        error = "Failed to parse JSON response from DOAJ Journal API."
    except Exception as e:
        error = f"An unexpected error occurred during DOAJ journal search: {e}"
    
    return doaj_journal_results, error


def generate_exa_research_report(query):
    """
    Generates a comprehensive research report using Exa.ai's research task API via OpenAI client.
    """
    report_content = ""
    try:
        completion = openai_exa_client.chat.completions.create(
            model="exa-research",
            messages=[
                {"role": "user", "content": f"Provide a comprehensive, concise, and structured summary of the research topic: {query}"}
            ],
            stream=False, # Changed to False for Django's request-response model
        )
        if completion.choices and completion.choices[0].message and completion.choices[0].message.content:
            report_content = completion.choices[0].message.content
        else:
            report_content = "No report content generated."

    except Exception as e:
        report_content = f"🚨 Error generating Exa.ai Research Report: {e}"
        
    return report_content


def perform_unified_search(query):
    """
    Performs search across Tavily, Google Scholar, Exa.ai (search), and DOAJ,
    and also initiates an Exa.ai research task.
    Returns merged individual results (articles and journals) and the Exa.ai research report.
    """
    all_processed_results = {}
    errors = []
    
    # Optimize the query once for all search engines
    optimized_scholar_exa_query, opt_schol_err = optimize_scholar_query(query)
    if opt_schol_err:
        errors.append(f"Scholar/Exa query optimization failed: {opt_schol_err}")
        optimized_scholar_exa_query = query # Fallback to original query
    
    doaj_optimized_query, opt_doaj_err = optimize_doaj_query(query) # NEW: Specific optimization for DOAJ
    if opt_doaj_err:
        errors.append(f"DOAJ query optimization failed: {opt_doaj_err}")
        doaj_optimized_query = query # Fallback to original query

    # 1. Search with Tavily (using optimized query and domain filter)
    tavily_results, tavily_error = search_tavily(optimized_scholar_exa_query)
    if tavily_error:
        errors.append(f"Tavily search encountered an issue: {tavily_error}")
    for result in tavily_results:
        url = result.get('url')
        if url:
            all_processed_results[url] = {
                "title": result.get('title', 'No Title'),
                "url": url,
                "content_snippet": result.get('content', 'No snippet available.'),
                "source_type": result.get('source', 'Website'),
                "query": query, # Store original query
                "optimized_query": optimized_scholar_exa_query, # Store optimized query
                "summary": None,
                "annotation": None,
                "authors": "",
                "year": "",
                "pdf_url": "",
                "main_pub_url": "",
                "doi": "",
                "journal_name": "",
                "volume": "",
                "pages": "",
                "publisher": "", # Default for non-journal entries
                "issn": "" # Default for non-journal entries
            }

    # 2. Search with Google Scholar (already uses optimized query)
    scholar_results, scholar_error = search_google_scholar(optimized_scholar_exa_query)
    if scholar_error:
        errors.append(f"Google Scholar search encountered an issue: {scholar_error}")
    for result in scholar_results:
        url = result.get('url')
        if url:
            existing_data = all_processed_results.get(url, {})
            all_processed_results[url] = {
                "title": result.get('title', existing_data.get('title', 'No Title')),
                "url": url,
                "content_snippet": result.get('content_snippet', existing_data.get('content_snippet', 'No snippet available.')),
                "source_type": result.get('source_type', existing_data.get('source_type', 'Website')),
                "query": query,
                "optimized_query": optimized_scholar_exa_query,
                "summary": existing_data.get('summary'),
                "annotation": existing_data.get('annotation'),
                "authors": result.get('authors', existing_data.get('authors', '')),
                "year": result.get('year', existing_data.get('year', '')),
                "pdf_url": result.get('pdf_url', existing_data.get('pdf_url', '')),
                "main_pub_url": result.get('main_pub_url', existing_data.get('main_pub_url', '')),
                "doi": result.get('doi', existing_data.get('doi', '')),
                "journal_name": result.get('journal_name', existing_data.get('journal_name', '')),
                "volume": result.get('volume', existing_data.get('volume', '')),
                "pages": result.get('pages', existing_data.get('pages', '')),
                "publisher": "", # Default for articles
                "issn": "" # Default for articles
            }

    # 3. Search with Exa.ai (for individual articles/snippets, using optimized query and domain filter)
    exa_search_results, exa_search_error = search_exa(optimized_scholar_exa_query)
    if exa_search_error:
        errors.append(f"Exa.ai individual article search encountered an issue: {exa_search_error}")
    for result in exa_search_results:
        url = result.get('url')
        if url:
            existing_data = all_processed_results.get(url, {})
            all_processed_results[url] = {
                "title": result.get('title', existing_data.get('title', 'No Title')),
                "url": url,
                "content_snippet": result.get('content_snippet') if len(result.get('content_snippet', '')) > len(existing_data.get('content_snippet', '')) else existing_data.get('content_snippet', 'No snippet available.'),
                "source_type": result.get('source_type', existing_data.get('source_type', 'Website')),
                "query": query,
                "optimized_query": optimized_scholar_exa_query,
                "summary": existing_data.get('summary'),
                "annotation": existing_data.get('annotation'),
                "authors": result.get('authors', existing_data.get('authors', '')),
                "year": result.get('year', existing_data.get('year', '')),
                "pdf_url": result.get('pdf_url', existing_data.get('pdf_url', '')),
                "main_pub_url": result.get('main_pub_url', existing_data.get('main_pub_url', '')),
                "doi": result.get('doi', existing_data.get('doi', '')),
                "journal_name": result.get('journal_name', existing_data.get('journal_name', '')),
                "volume": result.get('volume', existing_data.get('volume', '')), 
                "pages": result.get('pages', existing_data.get('pages', '')),
                "publisher": "", # Default for articles
                "issn": "" # Default for articles
            }

    # 4. Search with DOAJ Articles (using DOAJ-specific optimized query)
    doaj_search_results, doaj_search_error = search_doaj(doaj_optimized_query)
    if doaj_search_error:
        errors.append(f"DOAJ article search encountered an issue: {doaj_search_error}")
    for result in doaj_search_results:
        url = result.get('url')
        if url:
            existing_data = all_processed_results.get(url, {})
            all_processed_results[url] = {
                "title": result.get('title', existing_data.get('title', 'No Title')),
                "url": url,
                "content_snippet": result.get('content_snippet') if len(result.get('content_snippet', '')) > len(existing_data.get('content_snippet', '')) else existing_data.get('content_snippet', 'No snippet available.'),
                "source_type": result.get('source_type', existing_data.get('source_type', 'Website')),
                "query": query,
                "optimized_query": doaj_optimized_query, # Store DOAJ optimized query
                "summary": existing_data.get('summary'),
                "annotation": existing_data.get('annotation'),
                "authors": result.get('authors', existing_data.get('authors', '')),
                "year": result.get('year', existing_data.get('year', '')),
                "pdf_url": result.get('pdf_url', existing_data.get('pdf_url', '')),
                "main_pub_url": result.get('main_pub_url', existing_data.get('main_pub_url', '')),
                "doi": result.get('doi', existing_data.get('doi', '')),
                "journal_name": result.get('journal_name', existing_data.get('journal_name', '')),
                "volume": result.get('volume', existing_data.get('volume', '')),
                "pages": result.get('pages', existing_data.get('pages', '')),
                "publisher": "", # Default for articles
                "issn": "" # Default for articles
            }
    
    # NEW: 5. Search with DOAJ Journals (using DOAJ-specific optimized query)
    doaj_journal_search_results, doaj_journal_search_error = search_doaj_journals(doaj_optimized_query)
    if doaj_journal_search_error:
        errors.append(f"DOAJ journal search encountered an issue: {doaj_journal_search_error}")
    for result in doaj_journal_search_results:
        url = result.get('url')
        if url:
            existing_data = all_processed_results.get(url, {})
            all_processed_results[url] = {
                "title": result.get('title', existing_data.get('title', 'No Title')),
                "url": url,
                "content_snippet": result.get('content_snippet', existing_data.get('content_snippet', 'No snippet available.')),
                "source_type": result.get('source_type', existing_data.get('source_type', 'Website')), # This will be "DOAJ Journal"
                "query": query,
                "optimized_query": doaj_optimized_query, # Store DOAJ optimized query
                "summary": existing_data.get('summary'), # Will be None for journals
                "annotation": existing_data.get('annotation'), # Will be None for journals
                "authors": result.get('authors', existing_data.get('authors', '')), # Empty for journals
                "year": result.get('year', existing_data.get('year', '')), # Empty for journals
                "pdf_url": result.get('pdf_url', existing_data.get('pdf_url', '')), # Empty for journals
                "main_pub_url": result.get('main_pub_url', existing_data.get('main_pub_url', '')), # Journal homepage
                "doi": result.get('doi', existing_data.get('doi', '')), # Empty for journals
                "journal_name": result.get('journal_name', existing_data.get('journal_name', '')), # Journal title
                "volume": result.get('volume', existing_data.get('volume', '')), # Empty for journals
                "pages": result.get('pages', existing_data.get('pages', '')), # Empty for journals
                "publisher": result.get('publisher', existing_data.get('publisher', '')), # NEW
                "issn": result.get('issn', existing_data.get('issn', '')), # NEW
            }
    # 5. Generate Exa.ai Research Report (separate, synthesized output, using original query)
    # The research task is a synthesis, so the original, broader query is often more suitable here.
    exa_research_report = generate_exa_research_report(query)

    return list(all_processed_results.values()), exa_research_report, errors


def generate_gemini(prompt):
    """Generates text using the Gemini API, handling potential blocks."""
    try:
        response = gemini_model.generate_content(prompt)
        if not response.parts:
            if response.candidates and response.candidates[0].finish_reason != "STOP":
                 block_reason = response.candidates[0].finish_reason
                 safety_ratings = response.prompt_feedback.safety_ratings if response.prompt_feedback else "N/A"
                 error_msg = f"Content generation blocked by API. Reason: {block_reason}. Safety Ratings: {safety_ratings}"
                 return None, error_msg
            else:
                 return None, "Empty response from AI."
        return response.text, None
    except Exception as e:
        return None, str(e)

def generate_structured_summary_prompt(title, authors, year, journal_name, doi, content_to_summarize, url=None):
    """
    Creates a prompt for summarizing a text content according to a strict 10-point format.
    It summarizes the provided `content_to_summarize`.
    """
    citation_parts = []
    if authors: citation_parts.append(authors)
    if year: citation_parts.append(f"({year})")
    if title: citation_parts.append(title)
    if journal_name: citation_parts.append(journal_name)
    if doi: citation_parts.append(f"DOI:{doi}")
    
    citation_string_for_prompt = ", ".join(citation_parts) if citation_parts else "Not available in snippet."

    prompt_template = f"""
    You are an AI assistant specialized in summarizing academic articles. Your task is to extract specific information from the provided text content (which could be an abstract or a full article) and present it in a structured format.

    Follow these instructions precisely:
    - For each section below, extract information *only* from the "Text Content" provided.
    - If a piece of information for a specific section is not explicitly present in the "Text Content", **do not include that section (its number, bold heading, and content) in the output at all.** Only include sections for which you can provide concrete information.
    - Keep sentences clear, small, and in simple language. Aim for 3-4 sentences per section if content allows.
    - Do not add any introductory or concluding remarks outside the specified format.

    Here is the text content to summarize:
    ---
    {content_to_summarize[:10000]}
    ---

    Here is the required summary format. Only include sections for which you found information:

    **1. Full Citation of Article**
    {citation_string_for_prompt}

    **2. Research Problem / Aim of the article**
    Extract the central problem, question, or gap in knowledge that this article aims to address. Look for phrases like "This paper investigates...", "The aim of this study is...", "We address the challenge of...", "The problem explored is...".

    **3. Objectives of research or article**
    Identify the specific goals or objectives the authors set out to achieve. Look for phrases such as "The objectives include...", "This study aims...", "Our goal was to...".

    **4. Methodology used by the author/researcher**
    Describe how the research was conducted. Look for details on the type of research (e.g., qualitative, quantitative, mixed methods), data collection methods (e.g., surveys, interviews, experiments, simulations, literature review, secondary data analysis), and any mention of sample size, participants, or study location.

    **5. Key Findings of article**
    Summarize the most important results, discoveries, or conclusions drawn directly from the research. Look for phrases like "The results show...", "We found that...", "Key findings include...", "The primary outcome was...".

    **6. Discussion / Interpretation in short if any given in an article**
    Explain what the key findings mean and how they relate to existing knowledge or theories, if discussed in the provided content. Look for sections where authors interpret their results, compare them to previous studies, or explain their significance.

    **7. The Conclusion of research or Article in very simple language points by points**
    State the final conclusion(s) reached by the authors based on their findings. This is often a concise summary of the main outcome.

    **8. Recommendations / Implications if given in article**
    Note any suggestions for future research, practical applications, policy recommendations, or broader societal implications mentioned by the authors.

    **9. Limitations (if available)**
    Identify any constraints, weaknesses, or potential biases of the study acknowledged by the authors. Look for phrases like "Limitations of this study include...", "It is important to note that...", "Future research should address...".

    **10. Keywords**
    List 4–6 keywords that best describe the article's content. Look for an explicit "Keywords:" section or infer them from the most frequently used relevant terms in the text.
    """
    return prompt_template


def generate_annotation_prompt(title, url, query, summary, authors="", year=""):
    """Creates a prompt for generating an annotated bibliography entry."""
    author_info = f"Authors: {authors}\n" if authors else ""
    year_info = f"Year: {year}\n" if year else ""

    return f"""
    Generate an annotated bibliography entry for the following resource:
    Title: {title}
    {author_info}{year_info}URL: {url}
    Original Search Query: "{query}"
    Summary of Resource:
    ---
    {summary}
    ---
    Instructions: Write a concise annotation (75-150 words) based *only* on the provided summary. Describe the main topic/argument, assess relevance to the original query, mention key findings if available, and evaluate its potential usefulness for research on "{query}". Format as a standard annotation paragraph.
    """

# --- Citation Formatting Functions (NEW) ---

def _split_and_parse_authors(authors_str):
    """Helper to split and parse author names from a string."""
    if not authors_str:
        return []
    
    # Split by common separators like ", ", " and ", "&"
    raw_names = re.split(r',\s*| and | & ', authors_str)
    
    parsed_names = []
    for name in raw_names:
        if not name.strip():
            continue
        
        # Try to handle "Last, First" or "First Last"
        if ',' in name:
            parts = [p.strip() for p in name.split(',', 1)]
            parsed_names.append({'last': parts[0], 'first': parts[1] if len(parts) > 1 else ''})
        else:
            # Handle "First Last" or "F. Last"
            parts = name.split(' ')
            if len(parts) > 1:
                last_name = parts[-1]
                first_name = ' '.join(parts[:-1])
                parsed_names.append({'last': last_name, 'first': first_name})
            else:
                parsed_names.append({'last': name, 'first': ''})
    return parsed_names

def format_authors_mla(authors_str):
    parsed_names = _split_and_parse_authors(authors_str)
    if not parsed_names: return ""

    if len(parsed_names) == 1:
        return f"{parsed_names[0]['last']}, {parsed_names[0]['first']}".strip(', ')
    elif len(parsed_names) == 2:
        return f"{parsed_names[0]['last']}, {parsed_names[0]['first']}, and {parsed_names[1]['first']} {parsed_names[1]['last']}".strip(', ')
    else: # 3 or more authors for MLA 9th ed. bibliography
        return f"{parsed_names[0]['last']}, {parsed_names[0]['first']}, et al."

def format_authors_apa(authors_str):
    parsed_names = _split_and_parse_authors(authors_str)
    if not parsed_names: return ""

    formatted_names = []
    for p_name in parsed_names:
        last_name = p_name['last']
        first_initials = ''.join([part[0].upper() + '.' for part in p_name['first'].split(' ') if part.strip()])
        formatted_names.append(f"{last_name}, {first_initials}".strip(', '))
    
    if len(formatted_names) == 1:
        return formatted_names[0]
    elif len(formatted_names) == 2:
        return f"{formatted_names[0]} & {formatted_names[1]}"
    else: # APA 7th: for 3 to 20 authors, list all. For 21+, list first 19, ..., last.
          # Simplifying to et al. for 3+ for brevity and consistency with image style.
        return f"{formatted_names[0]} et al."

def format_authors_chicago(authors_str):
    # Chicago (Notes and Bibliography style) for bibliography entries is similar to MLA for full names
    return format_authors_mla(authors_str)

def format_authors_harvard(authors_str):
    parsed_names = _split_and_parse_authors(authors_str)
    if not parsed_names: return ""

    formatted_names = []
    for p_name in parsed_names:
        last_name = p_name['last']
        first_initials = ''.join([part[0].upper() + '.' for part in p_name['first'].split(' ') if part.strip()])
        formatted_names.append(f"{last_name}, {first_initials}".strip(', '))
    
    if len(formatted_names) == 1:
        return formatted_names[0]
    elif len(formatted_names) == 2:
        return f"{formatted_names[0]} and {formatted_names[1]}"
    else: # Simplifying to et al. for 3+
        return f"{formatted_names[0]} et al."

def format_authors_vancouver(authors_str):
    parsed_names = _split_and_parse_authors(authors_str)
    if not parsed_names: return ""
    
    formatted_names = []
    for p_name in parsed_names:
        last_name = p_name['last']
        first_initials = ''.join([part[0].upper() for part in p_name['first'].split(' ') if part.strip()]) # No periods for initials
        formatted_names.append(f"{last_name} {first_initials}".strip())
    
    return ", ".join(formatted_names)

def generate_citations(item):
    title = item.get('title', 'Untitled')
    authors_str = item.get('authors', '')
    year = item.get('year', '')
    journal_name = item.get('journal_name', '')
    volume = item.get('volume', '')
    pages = item.get('pages', '')
    # doi = item.get('doi', '') # Not explicitly used in the image examples for the main citation string
    # url = item.get('url', '') # Not explicitly used in the image examples for the main citation string

    citations = {}

    # MLA
    mla_authors = format_authors_mla(authors_str)
    mla_title = f'"{title}."'
    mla_journal_vol_pages = ""
    if journal_name:
        mla_journal_vol_pages += f"___{journal_name}___"
    if volume:
        mla_journal_vol_pages += f" {volume}"
    if year:
        mla_journal_vol_pages += f" ({year})"
    if pages:
        mla_journal_vol_pages += f": {pages}"
    if mla_journal_vol_pages:
        mla_journal_vol_pages += "."
    citations["MLA"] = f"{mla_authors} {mla_title} {mla_journal_vol_pages}".strip()

    # APA
    apa_authors = format_authors_apa(authors_str)
    apa_year = f"({year})." if year else ""
    apa_title_part = f"{title}." if title else ""
    apa_journal_vol_pages = ""
    if journal_name:
        apa_journal_vol_pages += f"___{journal_name}___"
    if volume:
        apa_journal_vol_pages += f", {volume}"
    if pages:
        apa_journal_vol_pages += f", S{pages}" if "S" in pages else f", {pages}" # APA often uses "S" for supplementary
    if apa_journal_vol_pages:
        apa_journal_vol_pages += "."
    citations["APA"] = f"{apa_authors} {apa_year} {apa_title_part} {apa_journal_vol_pages}".strip()

    # Chicago
    chicago_authors = format_authors_chicago(authors_str)
    chicago_title = f'"{title}."'
    chicago_journal_vol_pages = ""
    if journal_name:
        chicago_journal_vol_pages += f"___{journal_name}___"
    if volume:
        chicago_journal_vol_pages += f" {volume}"
    if year:
        chicago_journal_vol_pages += f" ({year})"
    if pages:
        chicago_journal_vol_pages += f": {pages}"
    if chicago_journal_vol_pages:
        chicago_journal_vol_pages += "."
    citations["Chicago"] = f"{chicago_authors} {chicago_title} {chicago_journal_vol_pages}".strip()

    # Harvard
    harvard_authors = format_authors_harvard(authors_str)
    harvard_year = f"{year}." if year else ""
    harvard_title_part = f"{title}." if title else ""
    harvard_journal_vol_pages = ""
    if journal_name:
        harvard_journal_vol_pages += f"___{journal_name}___"
    if volume:
        harvard_journal_vol_pages += f", {volume}"
    if pages:
        harvard_journal_vol_pages += f", pp.{pages}"
    if harvard_journal_vol_pages:
        harvard_journal_vol_pages += "."
    citations["Harvard"] = f"{harvard_authors} {harvard_year} {harvard_title_part} {harvard_journal_vol_pages}".strip()

    # Vancouver
    vancouver_authors = format_authors_vancouver(authors_str)
    vancouver_journal = journal_name.replace(' ', '').replace('-', '') if journal_name else "" # Vancouver often abbreviates journal names, but we'll just remove spaces/hyphens for simplicity
    vancouver_date_vol_pages = f"{year}"
    # The image shows "2017 Apr 1;69:S36-40". Month and day are not available in our data.
    # We'll stick to year, volume:pages format.
    if volume:
        vancouver_date_vol_pages += f" {volume}"
    if pages:
        vancouver_date_vol_pages += f":{pages}"
    
    citations["Vancouver"] = f"{vancouver_authors}. {title}. {vancouver_journal}. {vancouver_date_vol_pages}.".strip()

    return citations
