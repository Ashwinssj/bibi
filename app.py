import streamlit as st
import os
import json
import uuid
import datetime
import time
import redis
from tavily import TavilyClient
import google.generativeai as genai
from dotenv import load_dotenv
from serpapi import GoogleSearch
from exa_py import Exa
import re
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from pdfminer.high_level import extract_text

# --- Configuration & Initialization ---
load_dotenv() # Load .env file if present (for local development)

# Page Config
st.set_page_config(
    page_title="AI Bibliographer",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.title("📚 AI Bibliographer - Research Assistant")

# Define a list of common academic/journal domains for focused search
ACADEMIC_DOMAINS = [
    "pubmed.ncbi.nlm.nih.gov", "sciencedirect.com", "springer.com",
    "elsevier.com", "wiley.com", "ieee.org", "acm.org", "mdpi.com",
    "nature.com", "science.org", "frontiersin.org", "plos.org",
    "bmj.com", "jamanetwork.com", "nejm.org", "arxiv.org", "biorxiv.org",
    "jstor.org", "cambridge.org", "oup.com", "tandfonline.com",
    "researchgate.net", "academia.edu" # These are academic social networks, but often host papers
]


# API Keys and Clients Setup
@st.cache_resource
def configure_clients():
    """Loads secrets and configures API clients and Redis connection."""
    try:
        GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
        TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
        UPSTASH_REDIS_HOST = os.getenv("UPSTASH_REDIS_HOST")
        UPSTASH_REDIS_PORT = os.getenv("UPSTASH_REDIS_PORT")
        UPSTASH_REDIS_PASSWORD = os.getenv("UPSTASH_REDIS_PASSWORD")
        SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
        EXA_API_KEY = os.getenv("EXA_API_KEY")
        SCRAPERAPI_API_KEY = os.getenv("SCRAPERAPI_API_KEY")

        missing_vars = []
        if not GOOGLE_API_KEY: missing_vars.append("GOOGLE_API_KEY")
        if not TAVILY_API_KEY: missing_vars.append("TAVILY_API_KEY")
        if not UPSTASH_REDIS_HOST: missing_vars.append("UPSTASH_REDIS_HOST")
        if not UPSTASH_REDIS_PORT: missing_vars.append("UPSTASH_REDIS_PORT")
        if not UPSTASH_REDIS_PASSWORD: missing_vars.append("UPSTASH_REDIS_PASSWORD")
        if not SERPAPI_API_KEY: missing_vars.append("SERPAPI_API_KEY")
        if not EXA_API_KEY: missing_vars.append("EXA_API_KEY")
        if not SCRAPERAPI_API_KEY: missing_vars.append("SCRAPERAPI_API_KEY")

        if missing_vars:
            error_message = f"⚠️ Critical API keys or Redis connection details missing FROM ENV VARS: {', '.join(missing_vars)}. Please check Render Environment Variables."
            st.error(error_message)
            st.stop()

        # Configure Gemini
        genai.configure(api_key=GOOGLE_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')

        # Configure Tavily
        tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

        # Configure SerpApi
        serpapi_client = GoogleSearch

        # Configure Exa
        exa_client = Exa(api_key=EXA_API_KEY)

        # Configure Redis
        if not UPSTASH_REDIS_PORT or not UPSTASH_REDIS_PORT.isdigit():
            port_error_msg = f"🚨 Invalid or missing UPSTASH_REDIS_PORT: '{UPSTASH_REDIS_PORT}'. It must be a number."
            st.error(port_error_msg)
            st.stop()

        redis_client = redis.Redis(
            host=UPSTASH_REDIS_HOST,
            port=int(UPSTASH_REDIS_PORT),
            password=UPSTASH_REDIS_PASSWORD,
            ssl=True,
            decode_responses=True
        )
        redis_client.ping()

        return gemini_model, tavily_client, redis_client, serpapi_client, exa_client, SCRAPERAPI_API_KEY 

    except redis.exceptions.ConnectionError as e:
        error_msg = f"🚨 Could not connect to Redis: {e}. Please verify connection details and Upstash instance status."
        st.error(error_msg)
        st.stop()
    except ValueError as e:
        error_msg = f"🚨 Invalid Redis port configured: '{UPSTASH_REDIS_PORT}'. It must be a number. Error: {e}"
        st.error(error_msg)
        st.stop()
    except Exception as e:
        error_msg = f"🚨 An unexpected error occurred during configuration: {e}"
        st.error(error_msg)
        st.stop()


# Initialize clients
gemini_model, tavily_client, redis_client, serpapi_client, exa_client, SCRAPERAPI_API_KEY = configure_clients()

# --- Session State Initialization ---
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = [] # Initialize empty, history loaded on demand or after first message
if "current_tavily_results" not in st.session_state:
    st.session_state.current_tavily_results = []
if "current_processed_results" not in st.session_state:
    st.session_state.current_processed_results = {}
if "selected_folder_id" not in st.session_state:
    st.session_state.selected_folder_id = None # Initialize to None, meaning no folder is selected for display
if "folders_cache" not in st.session_state:
    st.session_state.folders_cache = None

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
        st.warning("Folder name cannot be empty.")
        return None
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
        st.toast(f"Folder '{name}' created.")
        st.session_state.folders_cache = None
        return folder_id
    except Exception as e:
        st.error(f"Error creating folder: {e}")
        return None

def get_folders():
    """Retrieves all folders from Redis, using cache if available."""
    if st.session_state.folders_cache is not None:
        return st.session_state.folders_cache
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
                     st.warning(f"Inconsistent data for folder key: {folder_keys[i]}. Removing.")
                     redis_client.srem(FOLDER_MASTER_LIST_KEY, folder_keys[i])

            folders.sort(key=lambda x: x.get('name', '').lower())
        st.session_state.folders_cache = folders
        return folders
    except Exception as e:
        st.error(f"Error retrieving folders: {e}")
        st.session_state.folders_cache = []
        return []

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
        st.toast("Folder deleted.")
        st.session_state.folders_cache = None
        if st.session_state.selected_folder_id == folder_id:
            st.session_state.selected_folder_id = None # Reset to None if deleted folder was selected
    except Exception as e:
        st.error(f"Error deleting folder: {e}")

# Library Items
def save_library_item(title, url, query, source_type, folder_id="root", summary="", annotation="", content_snippet="", authors="", year="", pdf_url="", main_pub_url="", doi="", journal_name="", volume="", pages=""):
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
        "volume": volume or "", # NEW
        "pages": pages or ""   # NEW
    }
    try:
        with redis_client.pipeline() as pipe:
            pipe.hset(item_key, mapping=item_data)
            if folder_id != "root":
                folder_items_key = f"{FOLDER_ITEMS_PREFIX}{folder_id}"
                pipe.sadd(folder_items_key, item_key)
            pipe.execute()
        st.toast(f"Item '{item_data['title'][:30]}...' saved.")
        return item_key
    except Exception as e:
        st.error(f"Error saving item: {e}")
        return None

def get_library_items(folder_id="root"):
    """Retrieves items, optionally filtered by folder_id."""
    try:
        item_keys = []
        if folder_id == "root":
            all_keys = redis_client.keys(f"{ITEM_PREFIX}*")
            if not all_keys: return []
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
                    st.warning(f"Inconsistent data for item key: {item_keys[i]}. Removing from folder set.")
                    if folder_id != "root":
                        redis_client.srem(f"{FOLDER_ITEMS_PREFIX}{folder_id}", item_keys[i])

            items.sort(key=lambda x: x.get('added_timestamp', ''), reverse=True)
        return items
    except Exception as e:
        st.error(f"Error retrieving library items: {e}")
        return []

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
        st.toast("Item deleted.")
    except Exception as e:
        st.error(f"Error deleting item: {e}")

# Chat History
def save_chat_message(session_id, role, content):
    """Appends a chat message to the Redis list for the session."""
    key = f"{CHAT_HISTORY_KEY_PREFIX}{session_id}"
    message = json.dumps({"role": role, "content": content})
    try:
        redis_client.rpush(key, message)
        redis_client.ltrim(key, -100, -1)
    except Exception as e:
        st.warning(f"Could not save chat message to Redis: {e}")

def load_chat_history(session_id):
    """Loads chat history for the session from Redis."""
    key = f"{CHAT_HISTORY_KEY_PREFIX}{session_id}"
    try:
        message_strings = redis_client.lrange(key, 0, -1)
        return [json.loads(msg) for msg in message_strings]
    except Exception as e:
        st.warning(f"Could not load chat history from Redis: {e}")
        return []

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
        st.info(f"🧠 Thinking... Searching with Tavily for: '{query}' (filtered by academic domains)")
        response = tavily_client.search(
            query=query,
            search_depth=search_depth,
            max_results=max_results,
            include_domains=ACADEMIC_DOMAINS # NEW: Apply domain filter
        )
        return response.get('results', []), None
    except Exception as e:
        st.error(f"Tavily search failed: {e}")
        return [], str(e)

def optimize_scholar_query(user_query):
    """Uses Gemini to optimize a user's natural language query for Google Scholar."""
    prompt_template = f"""
    You are an AI research assistant. Your task is to rephrase a user's natural language research query into a concise, effective, and keyword-rich search query suitable for academic databases like Google Scholar.

    Focus on:
    - Extracting the core topic.
    - Expanding abbreviations (e.g., "AI" to "Artificial Intelligence").
    - Removing conversational filler ("I want to research...", "articles about...").
    - Prioritizing academic terms.
    - **Crucially, the output should be a space-separated list of keywords. If a phrase contains multiple words, put that specific phrase in double quotes (e.g., "deep learning"). Do NOT use commas to separate keywords in the output query.**

    Example 1:
    User Query: "i want to research Articles in AI"
    Optimized Query: "Artificial Intelligence" research articles

    Example 2:
    User Query: "papers on climate change impact on agriculture"
    Optimized Query: "climate change" agriculture impact papers

    Example 3:
    User Query: "Machine learning with MCP model context protocol"
    Optimized Query: "Machine Learning" "MCP model" "context protocol"

    User Query: "{user_query}"
    Optimized Query:
    """
    st.info("🧠 Optimizing your query for Google Scholar...")
    optimized_query, error = generate_gemini(prompt_template)
    if error:
        st.warning(f"Could not optimize query, using original: {error}")
        return user_query
    
    optimized_query = optimized_query.strip()
    if optimized_query.startswith('"') and optimized_query.endswith('"') and optimized_query.count('"') == 2:
        optimized_query = optimized_query[1:-1]
    optimized_query = optimized_query.replace(',', ' ').strip()
    optimized_query = re.sub(r'\s+', ' ', optimized_query)

    return optimized_query


def search_google_scholar(query, num_results=7):
    """Performs a search using the SerpApi Google Scholar API with improved debugging."""
    scholar_results = []
    error = None
    
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        error = "SERPAPI_API_KEY environment variable is not set"
        st.error(error)
        return [], error
    
    params = {
        "engine": "google_scholar",
        "q": query,
        "num": num_results,
        "api_key": api_key
    }

    try:
        st.info(f"Searching Google Scholar for: '{query}'")

        search = GoogleSearch(params)
        results_json = search.get_dict()
        
        if results_json.get('search_metadata', {}).get('status') == 'Error':
            error = results_json.get('search_metadata', {}).get('error') or "Unknown SerpApi error."
            st.error(f"SerpApi Error: {error}")
            return [], error
        
        if 'error' in results_json:
            error = results_json['error']
            st.error(f"SerpApi Error: {error}")
            return [], error

        search_metadata = results.get('search_metadata', {})
        if search_metadata.get('status') != 'Success':
            error = f"Search not successful. Status: {search_metadata.get('status')}"
            st.error(error)
            return [], error

        organic_results = results_json.get('organic_results', [])

        if not organic_results:
            st.info("No organic results found from SerpApi Google Scholar.")
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
            volume = "" # NEW
            pages = ""  # NEW

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
                
                # Extract year
                year_match = re.search(r'\b(\d{4})\b', pub_summary)
                if year_match:
                    year = year_match.group(1)
                
                # Extract journal, volume, pages
                # Example: "P. Hamet, J. Tremblay - Metabolism - Clinical and Experimental, 2017, 69, S36-S40"
                # Example: "Nature, 2017, 541(7635), 1-10"
                # Example: "Journal of Science, 2020"
                
                # Remove author part if present to simplify parsing the rest
                temp_summary_for_parsing = pub_summary
                if authors:
                    temp_summary_for_parsing = temp_summary_for_parsing.replace(authors, '', 1).strip(' -').strip(',')
                
                # Try to extract volume and pages from the summary
                # Pattern: (volume)(optional sub-volume)(optional comma/space/colon)(pages)
                # This regex aims to capture:
                # 1. Volume (e.g., 69 or 135)
                # 2. Optional sub-volume (e.g., (1))
                # 3. Pages (e.g., S36-S40 or 1-10)
                vol_pages_match = re.search(r'(\d+)(?:\((\d+)\))?(?:,\s*|:\s*|\s*)(S?\d+-\d+|\d+-\d+)', temp_summary_for_parsing)
                if vol_pages_match:
                    volume = vol_pages_match.group(1)
                    pages = vol_pages_match.group(3) # Group 3 captures the pages (S36-S40 or 1-10)
                else: # Try just volume if no pages
                    vol_match = re.search(r'\b(\d+)\b', temp_summary_for_parsing)
                    if vol_match and vol_match.group(1) != year: # Ensure it's not the year itself
                        volume = vol_match.group(1)

                # Extract journal name:
                # It's usually the part between authors and the year/volume/pages info
                journal_candidate = temp_summary_for_parsing
                if year:
                    journal_candidate = journal_candidate.replace(year, '', 1).strip(',').strip()
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
                    "volume": volume, # NEW
                    "pages": pages   # NEW
                })
            else:
                st.warning(f"Skipping a Google Scholar result due to missing URL: {title}")

    except Exception as e:
        error = f"SerpApi Google Scholar search failed: {e}"
        st.error(error)
    
    return scholar_results, error

def search_exa(query, num_results=7):
    """Performs a search using the Exa.ai API (document retrieval), now with domain filtering."""
    exa_results = []
    error = None
    try:
        st.info(f"🧠 Thinking... Searching with Exa.ai for individual articles: '{query}' (filtered by academic domains)")
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
                        "volume": volume, # NEW
                        "pages": pages   # NEW
                    })
                else:
                    st.warning(f"Skipping an Exa.ai search result due to missing URL: {title}")
        else:
            st.info("No individual articles found from Exa.ai search.")

    except Exception as e:
        error = f"Exa.ai search failed: {e}"
        st.error(error)
    
    return exa_results, error


def generate_exa_research_report(query, timeout_seconds=180, polling_interval=5):
    """
    Generates a comprehensive research report using Exa.ai's research task API.
    """
    st.info(f"🔬 Initiating Exa.ai Research Task for: '{query}'")
    try:
        task_stub = exa_client.research.create_task(
            instructions=f"Provide a comprehensive, concise, and structured summary of the research topic: {query}",
            model="exa-research",
            output_infer_schema=True
        )
        st.info(f"Exa.ai research task created (ID: {task_stub.id}). Polling for results... This may take a moment.")
        
        start_time = time.time()
        while True:
            elapsed_time = time.time() - start_time
            if elapsed_time > timeout_seconds:
                return "⏳ Exa.ai Research Task timed out after 3 minutes. Please try again or refine your query."
                
            task = exa_client.research.poll_task(task_stub.id)
            
            if task.status == "completed":
                if hasattr(task, 'results'):
                    if hasattr(task.results, 'answer'):
                        report_content = task.results.answer
                    elif hasattr(task, 'answer'):
                        report_content = task.answer
                    elif isinstance(task.results, dict) and 'answer' in task.results:
                        report_content = task.results['answer']
                    else:
                        report_content = "Exa.ai Research Task completed, but could not locate the answer in the response."
                        st.warning(report_content)
                else:
                    report_content = "Exa.ai Research Task completed, but no results object was found."
                    st.warning(report_content)
                break
            elif task.status in ["failed", "cancelled"]:
                report_content = f"❌ Exa.ai Research Task {task.status}: {task.error or 'No error message provided.'}"
                st.error(report_content)
                break
                
            # Provide periodic feedback to the user
            st.info(f"Task is still processing... ({int(elapsed_time)} seconds elapsed)")
            time.sleep(polling_interval)
    except Exception as e:
        report_content = f"🚨 Error generating Exa.ai Research Report: {e}"
        st.error(report_content)
        
    return report_content


def perform_unified_search(query):
    """
    Performs search across Tavily, Google Scholar, and Exa.ai (search),
    and also initiates an Exa.ai research task.
    Returns merged individual results and the Exa.ai research report.
    """
    all_processed_results = {}

    # Optimize the query once for all search engines
    optimized_query = optimize_scholar_query(query)

    # 1. Search with Tavily (using optimized query and domain filter)
    tavily_results, tavily_error = search_tavily(optimized_query) # NEW: Use optimized_query
    if tavily_error:
        st.warning(f"Tavily search encountered an issue: {tavily_error}")
    for result in tavily_results:
        url = result.get('url')
        if url:
            all_processed_results[url] = {
                "title": result.get('title', 'No Title'),
                "url": url,
                "content_snippet": result.get('content', 'No snippet available.'),
                "source_type": result.get('source', 'Website'),
                "query": query, # Store original query
                "optimized_query": optimized_query, # Store optimized query
                "summary": None,
                "annotation": None,
                "authors": "",
                "year": "",
                "pdf_url": "",
                "main_pub_url": "",
                "doi": "",
                "journal_name": "",
                "volume": "", # NEW
                "pages": ""   # NEW
            }

    # 2. Search with Google Scholar (already uses optimized query)
    scholar_results, scholar_error = search_google_scholar(optimized_query)
    if scholar_error:
        st.warning(f"Google Scholar search encountered an issue: {scholar_error}")
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
                "optimized_query": optimized_query,
                "summary": existing_data.get('summary'),
                "annotation": existing_data.get('annotation'),
                "authors": result.get('authors', existing_data.get('authors', '')),
                "year": result.get('year', existing_data.get('year', '')),
                "pdf_url": result.get('pdf_url', existing_data.get('pdf_url', '')),
                "main_pub_url": result.get('main_pub_url', existing_data.get('main_pub_url', '')),
                "doi": result.get('doi', existing_data.get('doi', '')),
                "journal_name": result.get('journal_name', existing_data.get('journal_name', '')),
                "volume": result.get('volume', existing_data.get('volume', '')), # NEW
                "pages": result.get('pages', existing_data.get('pages', ''))   # NEW
            }

    # 3. Search with Exa.ai (for individual articles/snippets, using optimized query and domain filter)
    exa_search_results, exa_search_error = search_exa(optimized_query) # NEW: Use optimized_query
    if exa_search_error:
        st.warning(f"Exa.ai individual article search encountered an issue: {exa_search_error}")
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
                "optimized_query": optimized_query,
                "summary": existing_data.get('summary'),
                "annotation": existing_data.get('annotation'),
                "authors": result.get('authors', existing_data.get('authors', '')),
                "year": result.get('year', existing_data.get('year', '')),
                "pdf_url": result.get('pdf_url', existing_data.get('pdf_url', '')),
                "main_pub_url": result.get('main_pub_url', existing_data.get('main_pub_url', '')),
                "doi": result.get('doi', existing_data.get('doi', '')),
                "journal_name": result.get('journal_name', existing_data.get('journal_name', '')),
                "volume": result.get('volume', existing_data.get('volume', '')), # NEW
                "pages": result.get('pages', existing_data.get('pages', ''))   # NEW
            }
    
    # 4. Generate Exa.ai Research Report (separate, synthesized output, using original query)
    # The research task is a synthesis, so the original, broader query is often more suitable here.
    exa_research_report = generate_exa_research_report(query) 

    return list(all_processed_results.values()), exa_research_report


def generate_gemini(prompt):
    """Generates text using the Gemini API, handling potential blocks."""
    try:
        response = gemini_model.generate_content(prompt)
        if not response.parts:
            if response.candidates and response.candidates[0].finish_reason != "STOP":
                 block_reason = response.candidates[0].finish_reason
                 safety_ratings = response.prompt_feedback.safety_ratings if response.prompt_feedback else "N/A"
                 error_msg = f"Content generation blocked by API. Reason: {block_reason}. Safety Ratings: {safety_ratings}"
                 st.warning(error_msg)
                 return None, error_msg
            else:
                 st.warning("Gemini returned an empty response.")
                 return None, "Empty response from AI."
        return response.text, None
    except Exception as e:
        st.error(f"Gemini generation failed: {e}")
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

# --- Streamlit UI ---

# --- Sidebar ---
with st.sidebar:
    st.image("https://docs.streamlit.io/logo.svg", width=100)
    st.header("Library & Settings")
    st.divider()

    # Folder Management
    st.subheader("Folders")

    def handle_create_folder():
        folder_name = st.session_state.new_folder_name_input
        if create_folder(folder_name):
            st.session_state.new_folder_name_input = ""

    with st.expander("➕ Create New Folder"):
        st.text_input("Folder Name", key="new_folder_name_input")
        st.button("Create", on_click=handle_create_folder)

    # Add a "Select a folder" option for initial state
    folder_options = {"None": "Select a folder to view..."}
    folder_options.update({"root": "All Items (Root)"})
    folder_options.update({f["id"]: f["name"] for f in get_folders()})

    current_folder_keys = list(folder_options.keys())
    
    # Determine the initial index for the radio button
    initial_radio_index = 0 # Default to "Select a folder to view..."
    if st.session_state.selected_folder_id in folder_options:
        initial_radio_index = current_folder_keys.index(st.session_state.selected_folder_id)
    elif st.session_state.selected_folder_id == "root": # Ensure "root" is handled if it was the old default
        initial_radio_index = current_folder_keys.index("root")


    selected_folder_id_from_radio = st.radio(
        "View Folder:",
        options=current_folder_keys,
        format_func=lambda f_id: folder_options.get(f_id, "Invalid Folder"),
        key="folder_selector_radio",
        index=initial_radio_index
    )

    # Update session state only if a different option is selected
    if selected_folder_id_from_radio != st.session_state.selected_folder_id:
        if selected_folder_id_from_radio == "None":
            st.session_state.selected_folder_id = None
        else:
            st.session_state.selected_folder_id = selected_folder_id_from_radio
        st.rerun()

    if st.session_state.selected_folder_id is not None and st.session_state.selected_folder_id != "None": # Ensure it's not the placeholder
        selected_folder_data = next((f for f in get_folders() if f['id'] == st.session_state.selected_folder_id), None)
        if st.session_state.selected_folder_id == "root":
            selected_folder_name = "All Items (Root)"
        elif selected_folder_data:
             selected_folder_key = selected_folder_data.get('folder_key')
             selected_folder_name = selected_folder_data.get('name', 'this folder')
             if selected_folder_key:
                st.divider()
                delete_placeholder = st.empty()
                if delete_placeholder.button(f"🗑️ Delete '{selected_folder_name}'", key=f"del_folder_{st.session_state.selected_folder_id}"):
                    st.session_state[f"confirm_delete_folder_{selected_folder_key}"] = True

                if st.session_state.get(f"confirm_delete_folder_{selected_folder_key}", False):
                     confirm = st.checkbox(f"Confirm permanent deletion of '{selected_folder_name}'?", key=f"confirm_del_check_{selected_folder_key}")
                     if confirm:
                         delete_folder(selected_folder_key)
                         del st.session_state[f"confirm_delete_folder_{selected_folder_key}"]
                         st.rerun()
                     elif not st.session_state.get(f"confirm_delete_folder_{selected_folder_key}", False):
                         pass

    st.divider()
    st.subheader("💬 Chat History")
    if st.button("Clear Chat Display"):
         st.session_state.messages = []
         st.rerun()

    # Chat history will only be loaded/displayed after the first message is sent
    # or if a specific "Load History" button is added.
    history_container = st.container(height=300)
    with history_container:
        if st.session_state.messages:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
        else:
            st.caption("Chat history is empty for this session.")

# --- Main Content Area ---

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Enter your research topic or query..."):
    # Load history only when a message is sent if it's currently empty
    if not st.session_state.messages:
        st.session_state.messages = load_chat_history(st.session_state.session_id)

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    save_chat_message(st.session_state.session_id, "user", prompt)

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        message_placeholder.markdown("🧠 Thinking... Searching across multiple sources (Tavily, Google Scholar, Exa.ai) and generating a research report...")
        
        st.session_state.current_processed_results = {}

        combined_results, exa_research_report = perform_unified_search(prompt)
        
        response_content = ""
        if combined_results:
            for res in combined_results:
                st.session_state.current_processed_results[res['url']] = res

            response_content += f"Found {len(combined_results)} potential sources for '{prompt}'. You can process them below."
        else:
            response_content += f"😕 Sorry, I couldn't find specific individual results for '{prompt}' from any source."

        response_content += "\n\n---\n\n**Exa.ai Research Report:**\n\n"
        response_content += exa_research_report
        response_content += "\n\n---\n"

        message_placeholder.markdown(response_content)

        st.session_state.messages.append({"role": "assistant", "content": response_content})
        save_chat_message(st.session_state.session_id, "assistant", response_content)
        
        if combined_results:
             st.rerun()


# --- Display Search Results for Processing ---
if st.session_state.current_processed_results:
    st.divider()
    st.subheader("🔬 Process Search Results")
    st.caption("Generate summaries, annotations, and save items to your library.")

    urls_to_process = list(st.session_state.current_processed_results.keys())

    for url in urls_to_process:
        if url not in st.session_state.current_processed_results:
            continue

        result_data = st.session_state.current_processed_results[url]
        title = result_data["title"]
        content_snippet = result_data["content_snippet"]
        query = result_data["query"]
        optimized_query = result_data.get("optimized_query", query)
        source_type = result_data["source_type"]
        authors = result_data.get("authors", "")
        year = result_data.get("year", "")
        # Corrected lines below:
        pdf_url = result_data.get("pdf_url")
        main_pub_url = result_data.get("main_pub_url")
        doi = result_data.get("doi", "")
        journal_name = result_data.get("journal_name", "")
        volume = result_data.get("volume", "")
        pages = result_data.get("pages", "")

        summary = result_data.get("summary")
        annotation = result_data.get("annotation") # Corrected this line too

        with st.container(border=True):
            st.markdown(f"**[{title}]({url})**") 
            if authors:
                st.caption(f"Authors: {authors}")
            if year:
                st.caption(f"Year: {year}")
            st.caption(f"Source Type: {source_type}")
            if doi:
                st.caption(f"DOI: {doi}")
            if journal_name:
                st.caption(f"Journal: {journal_name}")
            if volume:
                st.caption(f"Volume: {volume}")
            if pages:
                st.caption(f"Pages: {pages}")
            
            link_options = []
            if main_pub_url and url != main_pub_url:
                link_options.append(f"[Main Article]({main_pub_url})")
            if pdf_url and url != pdf_url:
                link_options.append(f"[PDF]({pdf_url})")
            
            if link_options:
                st.markdown(" | ".join(link_options))

            st.markdown(f"> {content_snippet[:300]}..." if len(content_snippet) > 300 else content_snippet)

            # Adjusted columns for Summarize, Access, Cite, Save
            action_cols = st.columns([0.2, 0.2, 0.2, 0.4])

            # Summarize Button
            with action_cols[0]:
                if st.button("📄 Summarize", key=f"summarize_{url}"):
                    with st.spinner("Preparing content for summary..."):
                        text_for_summary = content_snippet # Default fallback if all else fails
                        scraped_content = None
                        scrape_error = None

                        # Define URLs to attempt, prioritizing explicit PDF, then main_pub_url, then original url
                        urls_to_try = []
                        if pdf_url:
                            urls_to_try.append({"url": pdf_url, "type": "pdf"})
                        if main_pub_url and main_pub_url != pdf_url: # Try main_pub_url as HTML if different from PDF
                            urls_to_try.append({"url": main_pub_url, "type": "html"})
                        if url and url != pdf_url and url != main_pub_url: # Try original url as HTML if different
                            urls_to_try.append({"url": url, "type": "html"})
                        # If the original URL was the only one and it was a PDF, ensure it's tried as HTML too
                        if url and ".pdf" in url.lower() and not any(d['url'] == url and d['type'] == 'html' for d in urls_to_try):
                             urls_to_try.append({"url": url, "type": "html"})


                        for attempt in urls_to_try:
                            current_attempt_url = attempt["url"]
                            current_attempt_type = attempt["type"]

                            temp_scraped_content, temp_scrape_error = scrape_article_content(current_attempt_url)

                            if temp_scraped_content and len(temp_scraped_content) > 200: # Consider it successful if substantial content
                                scraped_content = temp_scraped_content
                                text_for_summary = scraped_content
                                break # Stop trying if we got good content
                            else:
                                scrape_error = temp_scrape_error # Keep the last error for potential display


                        # Final check before proceeding with summary generation
                        if not scraped_content or len(text_for_summary) < 200:
                            text_for_summary = content_snippet
                            if not text_for_summary:
                                st.error("No content available to summarize (PDF, HTML, or snippet failed/empty).")
                                continue

                        # --- Attempt Structured Summary ---
                        # The prompt itself is now expected to provide a general summary if structured fails.
                        prompt_structured_sum = generate_structured_summary_prompt(
                            title=title,
                            authors=authors,
                            year=year,
                            journal_name=journal_name,
                            doi=doi,
                            content_to_summarize=text_for_summary,
                            url=url
                        )
                        generated_summary, error_structured = generate_gemini(prompt_structured_sum)

                        if not error_structured and generated_summary:
                            st.session_state.current_processed_results[url]["summary"] = generated_summary
                            st.rerun()
                        else:
                            # If even the single structured prompt (with its internal fallback) fails, show error.
                            st.error(f"Summary generation failed: {error_structured or 'Unknown API error'}. Please check content or try again.")


            # Access Button (formerly Annotate)
            with action_cols[1]:
                can_access = summary is not None
                if st.button("Access", key=f"access_{url}", disabled=not can_access):
                    if can_access:
                        with st.spinner("Generating annotation..."):
                            prompt_ann = generate_annotation_prompt(title, url, optimized_query, summary, authors, year)
                            generated_annotation, error = generate_gemini(prompt_ann)
                            if not error and generated_annotation:
                                st.session_state.current_processed_results[url]["annotation"] = generated_annotation
                                st.rerun()
                            else:
                                st.error(f"Annotation failed: {error or 'Unknown error'}")

            # Cite Button (NEW)
            with action_cols[2]:
                if st.button("Cite", key=f"cite_search_{url}"):
                    citations = generate_citations(result_data)
                    st.session_state[f"show_citations_search_{url}"] = citations # Store for display

            # Save Button & Folder Selector
            with action_cols[3]:
                save_folder_options = {"root": "All Items (Root)"}
                save_folder_options.update({f.get('id'): f.get('name') for f in get_folders()})

                select_key = f"folder_select_{url}"
                selected_save_folder_id = st.selectbox(
                    "Save to:", options=list(save_folder_options.keys()),
                    format_func=lambda x: save_folder_options[x], key=select_key, label_visibility="collapsed"
                )

                if st.button("💾 Save", key=f"save_{url}"):
                    current_summary = result_data.get("summary")
                    current_annotation = result_data.get("annotation")

                    with st.spinner("Saving..."):
                        if not current_summary:
                            st.info("Generating summary before saving...")
                            
                            text_to_summarize_for_save = content_snippet # Default fallback
                            scraped_content_for_save = None
                            scrape_error_for_save = None

                            # Define URLs to attempt, prioritizing explicit PDF, then main_pub_url, then original url
                            urls_to_try_for_save = []
                            if pdf_url:
                                urls_to_try_for_save.append({"url": pdf_url, "type": "pdf"})
                            if main_pub_url and main_pub_url != pdf_url:
                                urls_to_try_for_save.append({"url": main_pub_url, "type": "html"})
                            if url and url != pdf_url and url != main_pub_url:
                                urls_to_try_for_save.append({"url": url, "type": "html"})
                            if url and ".pdf" in url.lower() and not any(d['url'] == url and d['type'] == 'html' for d in urls_to_try_for_save):
                                urls_to_try_for_save.append({"url": url, "type": "html"})

                            for attempt in urls_to_try_for_save:
                                current_attempt_url = attempt["url"]
                                current_attempt_type = attempt["type"]

                                temp_scraped_content, temp_scrape_error = scrape_article_content(current_attempt_url)

                                if temp_scraped_content and len(temp_scraped_content) > 200:
                                    scraped_content_for_save = temp_scraped_content
                                    text_to_summarize_for_save = scraped_content_for_save
                                    break
                                else:
                                    scrape_error_for_save = temp_scrape_error


                            if not scraped_content_for_save or len(text_to_summarize_for_save) < 200:
                                text_to_summarize_for_save = content_snippet
                                if not text_to_summarize_for_save:
                                    st.error("No content available to summarize for saving. Item not saved.")
                                    continue

                            # --- Attempt Structured Summary for Save ---
                            # The prompt itself is now expected to provide a general summary if structured fails.
                            prompt_structured_sum = generate_structured_summary_prompt(
                                title=title, authors=authors, year=year,
                                journal_name=journal_name, doi=doi,
                                content_to_summarize=text_to_summarize_for_save,
                                url=url
                            )
                            current_summary, error_structured_save = generate_gemini(prompt_structured_sum)

                            if error_structured_save or not current_summary:
                                st.error(f"Summary generation failed for saving: {error_structured_save}. Item not saved.")
                                continue
                            
                            st.session_state.current_processed_results[url]["summary"] = current_summary


                        if not current_annotation and current_summary:
                            st.info("Generating annotation before saving...")
                            prompt_ann = generate_annotation_prompt(title, url, optimized_query, current_summary, authors, year)
                            current_annotation, error_ann = generate_gemini(prompt_ann)
                            if error_ann or not current_annotation:
                                st.warning("Failed to generate annotation, saving without it.")
                                current_annotation = ""
                            st.session_state.current_processed_results[url]["annotation"] = current_annotation

                        saved_item_key = save_library_item(
                            title=title, url=url, query=query, source_type=source_type,
                            folder_id=selected_save_folder_id, summary=current_summary or "",
                            annotation=current_annotation or "", content_snippet=content_snippet,
                            authors=authors, year=year,
                            pdf_url=pdf_url, main_pub_url=main_pub_url,
                            doi=doi,
                            journal_name=journal_name,
                            volume=volume, # NEW
                            pages=pages   # NEW
                        )
                        if saved_item_key:
                             del st.session_state.current_processed_results[url]
                             st.rerun()

            if summary:
                with st.expander("View Generated Summary"):
                    st.markdown(summary)
            if annotation:
                 with st.expander("View Generated Annotation"):
                     st.markdown(annotation)
            
            # Display citations if available for search result (NEW)
            if st.session_state.get(f"show_citations_search_{url}"):
                with st.expander("View Citations", expanded=True):
                    for style, citation_text in st.session_state[f"show_citations_search_{url}"].items():
                        st.markdown(f"**{style}** {citation_text}")
                    # Removed st.rerun() here. The pop will trigger a rerun, which is sufficient.
                    st.button("Close Citations", key=f"close_cite_search_{url}", on_click=lambda: st.session_state.pop(f"show_citations_search_{url}", None))


# --- Conditional Display of Library Items ---
# This section will only appear if a folder is explicitly selected (i.e., not None or "None" placeholder)
if st.session_state.selected_folder_id is not None and st.session_state.selected_folder_id != "None":
    st.divider()
    selected_folder_name_display = folder_options.get(st.session_state.selected_folder_id, 'Unknown Folder')
    st.subheader(f" L📂 Library Items in '{selected_folder_name_display}'")

    library_items = get_library_items(st.session_state.selected_folder_id)

    if not library_items:
        st.info("No items found for this selection.")
    else:
        for item in library_items:
            item_key = item.get('item_key')
            if not item_key: continue

            with st.container(border=True):
                st.markdown(f"**[{item.get('title', 'No Title')}]({item.get('url', '#')})**")
                st.caption(f"Added: {item.get('added_timestamp', 'N/A').split('T')[0]} | Source: {item.get('source_type', 'N/A')}")
                
                author_year_info = []
                if item.get('authors'):
                    author_year_info.append(f"Authors: {item['authors']}")
                if item.get('year'):
                    author_year_info.append(f"Year: {item['year']}")
                if author_year_info:
                    st.caption(" | ".join(author_year_info))

                if item.get('journal_name'):
                    st.caption(f"Journal: {item['journal_name']}")
                if item.get('volume'):
                    st.caption(f"Volume: {item['volume']}")
                if item.get('pages'):
                    st.caption(f"Pages: {item['pages']}")

                if item.get('doi'):
                    st.caption(f"DOI: {item['doi']}")

                st.caption(f"Original Query: _{item.get('query', 'N/A')}_")

                link_options = []
                if item.get('main_pub_url') and item.get('url') != item.get('main_pub_url'):
                    link_options.append(f"[Main Article]({item['main_pub_url']})")
                if item.get('pdf_url') and item.get('url') != item.get('pdf_url'):
                    link_options.append(f"[PDF]({item['pdf_url']})")
                
                if link_options:
                    st.markdown(" | ".join(link_options))

                if item.get('summary'):
                    with st.expander("Summary"):
                        st.markdown(item['summary'])
                if item.get('annotation'):
                    with st.expander("Annotation"):
                        st.markdown(item['annotation'])
                if item.get('content_snippet'):
                    with st.expander("Original Snippet/Abstract"):
                        st.markdown(item['content_snippet'])

                # Buttons for Library Items
                lib_item_cols = st.columns([0.15, 0.85]) # Adjust column width for buttons
                with lib_item_cols[0]:
                    if st.button("Cite", key=f"cite_library_{item_key}"):
                        citations = generate_citations(item)
                        st.session_state[f"show_citations_lib_{item_key}"] = citations
                with lib_item_cols[1]:
                    if st.button("🗑️ Delete Item", key=f"delete_item_{item_key}", help="Permanently delete this item"):
                        if st.checkbox(f"Confirm delete?", key=f"confirm_delete_item_{item_key}", value=False):
                            delete_library_item(item_key)
                            st.rerun()

                # Display citations if available for library item (NEW)
                if st.session_state.get(f"show_citations_lib_{item_key}"):
                    with st.expander("View Citations", expanded=True):
                        for style, citation_text in st.session_state[f"show_citations_lib_{item_key}"].items():
                            st.markdown(f"**{style}** {citation_text}")
                        # Removed st.rerun() here. The pop will trigger a rerun, which is sufficient.
                        st.button("Close Citations", key=f"close_cite_lib_{item_key}", on_click=lambda: st.session_state.pop(f"show_citations_lib_{item_key}", None))

