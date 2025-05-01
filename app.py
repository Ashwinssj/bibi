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

# --- Configuration & Initialization ---
load_dotenv() # Load .env file if present (for local development)

# Page Config
st.set_page_config(
    page_title="Doc Siofbq - Research Assistant",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.title("📚 Doc Siofbq - Research Assistant")

# API Keys and Clients Setup
    # --- Temporarily modify for Render debugging ---
    # --- DEBUGGING VERSION - REMOVE st.secrets.get ---
@st.cache_resource
def configure_clients():
    """Loads secrets and configures API clients and Redis connection."""
    print("--- ENTERING configure_clients (DEBUGGING VERSION) ---") # Add entry print
    try:
        # ONLY use os.getenv for this test
        GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
        TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
        UPSTASH_REDIS_HOST = os.getenv("UPSTASH_REDIS_HOST")
        UPSTASH_REDIS_PORT = os.getenv("UPSTASH_REDIS_PORT")
        UPSTASH_REDIS_PASSWORD = os.getenv("UPSTASH_REDIS_PASSWORD")

            # Add print statements to see what's being loaded
        print(f"--- DEBUG ENV VARS ---")
        print(f"GOOGLE_API_KEY loaded: {GOOGLE_API_KEY is not None and len(str(GOOGLE_API_KEY)) > 0}")
        print(f"TAVILY_API_KEY loaded: {TAVILY_API_KEY is not None and len(str(TAVILY_API_KEY)) > 0}")
        print(f"UPSTASH_REDIS_HOST loaded: {UPSTASH_REDIS_HOST is not None and len(str(UPSTASH_REDIS_HOST)) > 0}")
        print(f"UPSTASH_REDIS_PORT loaded: {UPSTASH_REDIS_PORT is not None and len(str(UPSTASH_REDIS_PORT)) > 0}")
        print(f"UPSTASH_REDIS_PASSWORD loaded: {UPSTASH_REDIS_PASSWORD is not None and len(str(UPSTASH_REDIS_PASSWORD)) > 0}")
        # Optionally print the first few chars of the values to verify they look right (DON'T print full secrets)
        print(f"  GOOGLE_API_KEY start: {str(GOOGLE_API_KEY)[:5] if GOOGLE_API_KEY else 'None'}")
        print(f"  TAVILY_API_KEY start: {str(TAVILY_API_KEY)[:5] if TAVILY_API_KEY else 'None'}")
        print(f"  UPSTASH_REDIS_HOST start: {str(UPSTASH_REDIS_HOST)[:5] if UPSTASH_REDIS_HOST else 'None'}")
        print(f"  UPSTASH_REDIS_PORT: {UPSTASH_REDIS_PORT if UPSTASH_REDIS_PORT else 'None'}")
        print(f"  UPSTASH_REDIS_PASSWORD start: {str(UPSTASH_REDIS_PASSWORD)[:5] if UPSTASH_REDIS_PASSWORD else 'None'}")
        print(f"--- END DEBUG ENV VARS ---")

            # Check if any are missing
        missing_vars = []
        if not GOOGLE_API_KEY: missing_vars.append("GOOGLE_API_KEY")
        if not TAVILY_API_KEY: missing_vars.append("TAVILY_API_KEY")
        if not UPSTASH_REDIS_HOST: missing_vars.append("UPSTASH_REDIS_HOST")
        if not UPSTASH_REDIS_PORT: missing_vars.append("UPSTASH_REDIS_PORT")
        if not UPSTASH_REDIS_PASSWORD: missing_vars.append("UPSTASH_REDIS_PASSWORD")

        if missing_vars:
            error_message = f"⚠️ Critical API keys or Redis connection details missing FROM ENV VARS: {', '.join(missing_vars)}. Please check Render Environment Variables."
            print(f"ERROR: {error_message}") # Print error to logs
            st.error(error_message)
            st.stop()

            # --- Rest of the function ---
        print("--- CONFIGURING CLIENTS ---") # Add progress print

            # Configure Gemini
        genai.configure(api_key=GOOGLE_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')

            # Configure Tavily
        tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

            # Configure Redis
            # Add extra check for port validity before int() conversion
        if not UPSTASH_REDIS_PORT or not UPSTASH_REDIS_PORT.isdigit():
            port_error_msg = f"🚨 Invalid or missing UPSTASH_REDIS_PORT: '{UPSTASH_REDIS_PORT}'. It must be a number."
            print(f"ERROR: {port_error_msg}")
            st.error(port_error_msg)
            st.stop()

        redis_client = redis.Redis(
                host=UPSTASH_REDIS_HOST,
                port=int(UPSTASH_REDIS_PORT), # Ensure port is integer
                password=UPSTASH_REDIS_PASSWORD,
                ssl=True, # Upstash requires SSL
                decode_responses=True # Decode responses to strings
            )
            # Test Redis connection
        print("--- PINGING REDIS ---")
        redis_client.ping()
        print("--- REDIS PING SUCCESSFUL ---")

        print("--- RETURNING CLIENTS ---")
        return gemini_model, tavily_client, redis_client

    except redis.exceptions.ConnectionError as e:
        error_msg = f"🚨 Could not connect to Redis: {e}. Please verify connection details and Upstash instance status."
        print(f"ERROR: {error_msg}")
        st.error(error_msg)
        st.stop()
    except ValueError as e: # Catch potential int() error specifically
        error_msg = f"🚨 Invalid Redis port configured: '{UPSTASH_REDIS_PORT}'. It must be a number. Error: {e}"
        print(f"ERROR: {error_msg}")
        st.error(error_msg)
        st.stop()
    except Exception as e:
        error_msg = f"🚨 An unexpected error occurred during configuration: {e}"
        print(f"ERROR: {error_msg}")
        st.error(error_msg)
        st.stop()


# Initialize clients
gemini_model, tavily_client, redis_client = configure_clients()

# --- Session State Initialization ---
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    # Load chat history or initialize empty list
    st.session_state.messages = []
if "current_tavily_results" not in st.session_state:
    st.session_state.current_tavily_results = [] # Raw results from last search
if "current_processed_results" not in st.session_state:
    # Store results with generated summaries/annotations before saving {url: data}
    st.session_state.current_processed_results = {}
if "selected_folder_id" not in st.session_state:
    st.session_state.selected_folder_id = "root" # Default view is root/all
if "folders_cache" not in st.session_state:
    st.session_state.folders_cache = None # Cache for folder list

# --- Redis Data Structure Keys ---
FOLDER_MASTER_LIST_KEY = "folders" # Set containing all folder keys (e.g., folder:xyz)
FOLDER_PREFIX = "folder:" # Prefix for folder metadata Hashes
FOLDER_ITEMS_PREFIX = "folder_items:" # Prefix for Sets containing item keys for a folder
ITEM_PREFIX = "item:" # Prefix for item data Hashes
CHAT_HISTORY_KEY_PREFIX = "chat_history:" # Prefix for chat history Lists

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
                "id": folder_id, # Store the UUID part as ID
                "name": name,
                "created_timestamp": timestamp
            })
            pipe.sadd(FOLDER_MASTER_LIST_KEY, folder_key)
            pipe.execute()
        st.toast(f"Folder '{name}' created.")
        st.session_state.folders_cache = None # Invalidate cache
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
            # Ensure data integrity and add the full key back for reference if needed
            for i, data in enumerate(results):
                 if data and 'name' in data and 'id' in data:
                     data['folder_key'] = folder_keys[i] # Store the full key like 'folder:uuid'
                     folders.append(data)
                 else:
                     st.warning(f"Inconsistent data for folder key: {folder_keys[i]}. Removing.")
                     redis_client.srem(FOLDER_MASTER_LIST_KEY, folder_keys[i]) # Cleanup

            folders.sort(key=lambda x: x.get('name', '').lower())
        st.session_state.folders_cache = folders # Update cache
        return folders
    except Exception as e:
        st.error(f"Error retrieving folders: {e}")
        st.session_state.folders_cache = [] # Cache empty list on error
        return []

def delete_folder(folder_key):
    """Deletes a folder metadata and its associated items set."""
    folder_id = folder_key.split(":", 1)[1] # Extract UUID
    folder_items_key = f"{FOLDER_ITEMS_PREFIX}{folder_id}"
    try:
        # Optional: Check if folder contains items and decide behavior
        # item_count = redis_client.scard(folder_items_key)
        # if item_count > 0:
        #     st.warning(f"Deleting folder containing {item_count} items. Items will remain but be unlinked from this folder.")
            # Or implement item moving/deletion logic here

        with redis_client.pipeline() as pipe:
            pipe.delete(folder_key) # Delete folder metadata hash
            pipe.delete(folder_items_key) # Delete the set of items for this folder
            pipe.srem(FOLDER_MASTER_LIST_KEY, folder_key) # Remove from master list
            pipe.execute()
        st.toast("Folder deleted.")
        st.session_state.folders_cache = None # Invalidate cache
        # If the deleted folder was selected, reset selection to root
        if st.session_state.selected_folder_id == folder_id:
            st.session_state.selected_folder_id = "root"
    except Exception as e:
        st.error(f"Error deleting folder: {e}")

# Library Items
def save_library_item(title, url, query, source_type, folder_id="root", summary="", annotation="", tavily_snippet=""):
    """Saves a library item to Redis."""
    item_uuid = str(uuid.uuid4())
    item_key = f"{ITEM_PREFIX}{item_uuid}"
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    item_data = {
        "id": item_uuid, # Store just the UUID part
        "title": title or "Untitled",
        "url": url,
        "query": query or "",
        "summary": summary or "",
        "annotation": annotation or "",
        "tavily_snippet": tavily_snippet or "",
        "folder_id": folder_id if folder_id != "root" else "", # Store empty string for root
        "added_timestamp": timestamp,
        "source_type": source_type or "Website"
    }
    try:
        with redis_client.pipeline() as pipe:
            pipe.hset(item_key, mapping=item_data)
            # Add item key to the specified folder's set (if not root)
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
            # Get all item keys and filter for those whose 'folder_id' hash field is empty
            all_keys = redis_client.keys(f"{ITEM_PREFIX}*")
            if not all_keys: return []
            pipe = redis_client.pipeline()
            for key in all_keys:
                 pipe.hget(key, "folder_id")
            folder_ids = pipe.execute()
            item_keys = [key for key, f_id in zip(all_keys, folder_ids) if not f_id]
        else:
            # Get items from the specific folder set
            folder_items_key = f"{FOLDER_ITEMS_PREFIX}{folder_id}"
            item_keys = list(redis_client.smembers(folder_items_key))

        items = []
        if item_keys:
            pipe = redis_client.pipeline()
            for key in item_keys:
                pipe.hgetall(key)
            results = pipe.execute()
            # Add the full item_key back to the dict for reference
            for i, data in enumerate(results):
                 if data:
                    data['item_key'] = item_keys[i] # Store the full key like 'item:uuid'
                    items.append(data)
                 else:
                    # Handle inconsistency: Key in set but hash missing
                    st.warning(f"Inconsistent data for item key: {item_keys[i]}. Removing from folder set.")
                    if folder_id != "root":
                        redis_client.srem(f"{FOLDER_ITEMS_PREFIX}{folder_id}", item_keys[i])
                    # Consider deleting the orphaned key if desired: redis_client.delete(item_keys[i])

            items.sort(key=lambda x: x.get('added_timestamp', ''), reverse=True)
        return items
    except Exception as e:
        st.error(f"Error retrieving library items: {e}")
        return []

def delete_library_item(item_key):
    """Deletes an item from Redis and removes it from its folder set."""
    try:
        # Get folder_id from the item itself before deleting
        folder_id = redis_client.hget(item_key, "folder_id")

        with redis_client.pipeline() as pipe:
            pipe.delete(item_key)
            # Remove from folder set if it had a specific folder_id
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
        # Optional: Trim history
        redis_client.ltrim(key, -100, -1) # Keep last 100 messages
    except Exception as e:
        st.warning(f"Could not save chat message to Redis: {e}") # Non-critical

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

def search_tavily(query, search_depth="basic", max_results=7):
    """Performs a search using the Tavily API."""
    try:
        response = tavily_client.search(
            query=query, search_depth=search_depth, max_results=max_results
        )
        return response.get('results', []), None
    except Exception as e:
        st.error(f"Tavily search failed: {e}")
        return [], str(e)

def generate_gemini(prompt):
    """Generates text using the Gemini API, handling potential blocks."""
    try:
        response = gemini_model.generate_content(prompt)
        # More robust check for blocked content
        if not response.parts:
            if response.candidates and response.candidates[0].finish_reason != "STOP":
                 block_reason = response.candidates[0].finish_reason
                 safety_ratings = response.prompt_feedback.safety_ratings if response.prompt_feedback else "N/A"
                 error_msg = f"Content generation blocked by API. Reason: {block_reason}. Safety Ratings: {safety_ratings}"
                 st.warning(error_msg)
                 return None, error_msg
            else:
                 # Handle other cases like no response text
                 st.warning("Gemini returned an empty response.")
                 return None, "Empty response from AI."
        return response.text, None
    except Exception as e:
        st.error(f"Gemini generation failed: {e}")
        return None, str(e)

def generate_summary_prompt(content_snippet, url=None):
    """Creates a prompt for summarizing a text snippet."""
    return f"""
    Analyze the following text snippet from a web search result.
    Summarize the key information relevant to a researcher (2-4 sentences), focusing on main points, findings, or arguments.

    Text Snippet:
    ---
    {content_snippet[:10000]}
    ---
    {f"Source URL (for context only): {url}" if url else ""}
    """

def generate_annotation_prompt(title, url, query, summary):
    """Creates a prompt for generating an annotated bibliography entry."""
    return f"""
    Generate an annotated bibliography entry for the following resource:
    Title: {title}
    URL: {url}
    Original Search Query: "{query}"
    Summary of Resource:
    ---
    {summary}
    ---
    Instructions: Write a concise annotation (75-150 words) based *only* on the provided summary. Describe the main topic/argument, assess relevance to the original query, mention key findings if available, and evaluate its potential usefulness for research on "{query}". Format as a standard annotation paragraph.
    """

# --- Streamlit UI ---

# --- Sidebar ---
# --- Streamlit UI ---

# --- Sidebar ---
with st.sidebar:
    st.image("https://docs.streamlit.io/logo.svg", width=100) # Placeholder logo
    st.header("Library & Settings")
    st.divider()

    # Folder Management
    st.subheader("Folders")

    # Define the callback function for the create button
    def handle_create_folder():
        folder_name = st.session_state.new_folder_name_input
        if create_folder(folder_name): # Check if folder creation was successful
            # Clear the input field value in session state *after* successful creation
            st.session_state.new_folder_name_input = ""
        # No explicit st.rerun() needed here, button clicks trigger it.

    with st.expander("➕ Create New Folder"):
        # Instantiate the text input widget, its value is controlled by session state
        st.text_input("Folder Name", key="new_folder_name_input")
        # Use the on_click callback
        st.button("Create", on_click=handle_create_folder)

        # --- The old logic inside if st.button(...) is removed ---
        # if st.button("Create"):
        #     create_folder(new_folder_name) # Moved to callback
        #     st.session_state.new_folder_name_input = "" # Moved to callback & causes error here
        #     st.rerun() # Moved to callback (implicitly)

    # Folder Selection List
    folders = get_folders()
    folder_options = {"root": "All Items (Root)"}
    folder_options.update({f["id"]: f["name"] for f in folders})

    # Use radio buttons for folder selection
    # Get the current index based on session state before creating the widget
    current_folder_keys = list(folder_options.keys())
    try:
        current_index = current_folder_keys.index(st.session_state.selected_folder_id)
    except ValueError:
        # Handle case where selected folder might have been deleted - default to root
        st.session_state.selected_folder_id = "root"
        current_index = 0

    selected_folder_id = st.radio(
        "View Folder:",
        options=current_folder_keys,
        format_func=lambda f_id: folder_options.get(f_id, "Invalid Folder"), # Use .get for safety
        key="folder_selector_radio",
        index=current_index # Maintain selection using the calculated index
    )

    # Update session state if selection changes
    if selected_folder_id != st.session_state.selected_folder_id:
        st.session_state.selected_folder_id = selected_folder_id
        st.rerun()

    # Display Delete button only if a specific folder (not root) is selected
    if st.session_state.selected_folder_id != "root":
        selected_folder_data = next((f for f in folders if f['id'] == st.session_state.selected_folder_id), None)
        if selected_folder_data:
             selected_folder_key = selected_folder_data.get('folder_key')
             selected_folder_name = selected_folder_data.get('name', 'this folder')
             if selected_folder_key:
                st.divider() # Add some spacing
                # Confirmation moved into a separate button press flow for clarity
                delete_placeholder = st.empty()
                if delete_placeholder.button(f"🗑️ Delete '{selected_folder_name}'", key=f"del_folder_{st.session_state.selected_folder_id}"):
                    st.session_state[f"confirm_delete_folder_{selected_folder_key}"] = True # Set flag to show confirmation

                # Show confirmation checkbox only if the flag is set
                if st.session_state.get(f"confirm_delete_folder_{selected_folder_key}", False):
                     confirm = st.checkbox(f"Confirm permanent deletion of '{selected_folder_name}'?", key=f"confirm_del_check_{selected_folder_key}")
                     if confirm:
                         delete_folder(selected_folder_key)
                         # Clean up confirmation state variable
                         del st.session_state[f"confirm_delete_folder_{selected_folder_key}"]
                         st.rerun() # Refresh the UI
                     elif not st.session_state.get(f"confirm_delete_folder_{selected_folder_key}", False): # If checkbox unticked or button not pressed again
                         # Allow cancelling by simply not checking the box and letting the script rerun
                         pass


    st.divider()
    # Chat History Display
    st.subheader("💬 Chat History")
    if st.button("Clear Chat Display"):
         st.session_state.messages = []
         # Note: This only clears the display, not Redis history for the session
         st.rerun()

    # Load history if messages list is empty
    if not st.session_state.messages:
         st.session_state.messages = load_chat_history(st.session_state.session_id)

    history_container = st.container(height=300)
    with history_container:
        if st.session_state.messages:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
        else:
            st.caption("Chat history is empty for this session.")

# --- Main Content Area ---
# (Rest of the main content code remains the same)
# ...


# --- Main Content Area ---

# Display existing messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Accept user input using st.chat_input
if prompt := st.chat_input("Enter your research topic or query..."):
    # Add user message to display and session state
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Save user message to Redis
    save_chat_message(st.session_state.session_id, "user", prompt)

    # --- Perform Search and Display Results ---
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        message_placeholder.markdown("🧠 Thinking... Searching the web with Tavily...")

        # Clear previous results before new search
        st.session_state.current_tavily_results = []
        st.session_state.current_processed_results = {}

        search_results, error = search_tavily(prompt)

        if error:
            response_text = f"Search failed: {error}"
            message_placeholder.error(response_text)
        elif search_results:
            st.session_state.current_tavily_results = search_results # Store raw results
            response_text = f"Found {len(search_results)} potential sources for '{prompt}'. You can process them below."
            message_placeholder.markdown(response_text)

            # Prepare results for processing section
            for result in search_results:
                url = result.get('url')
                if url: # Use URL as the key
                    st.session_state.current_processed_results[url] = {
                        "title": result.get('title', 'No Title'),
                        "snippet": result.get('content', 'No snippet available.'),
                        "source_type": result.get('source', 'Website'),
                        "query": prompt,
                        "summary": None,
                        "annotation": None
                    }
        else:
            response_text = f"😕 Sorry, I couldn't find specific results for '{prompt}'. Try refining your query."
            message_placeholder.markdown(response_text)

        # Save assistant's response to chat history
        st.session_state.messages.append({"role": "assistant", "content": response_text})
        save_chat_message(st.session_state.session_id, "assistant", response_text)
        # Rerun needed to display the processing section below if results were found
        if search_results:
             st.rerun()


# --- Display Search Results for Processing ---
if st.session_state.current_processed_results:
    st.divider()
    st.subheader("🔬 Process Search Results")
    st.caption("Generate summaries, annotations, and save items to your library.")

    urls_to_process = list(st.session_state.current_processed_results.keys())

    for url in urls_to_process:
        # Check if URL still exists in the dict (might be removed after saving)
        if url not in st.session_state.current_processed_results:
            continue

        result_data = st.session_state.current_processed_results[url]
        title = result_data["title"]
        snippet = result_data["snippet"]
        query = result_data["query"]
        source_type = result_data["source_type"]
        summary = result_data.get("summary")
        annotation = result_data.get("annotation")

        with st.container(border=True):
            st.markdown(f"**[{title}]({url})**")
            st.caption(f"Source Type: {source_type}")
            st.markdown(f"> {snippet[:300]}..." if len(snippet) > 300 else snippet) # Preview

            action_cols = st.columns([0.25, 0.25, 0.5]) # Adjust ratios

            # Summarize Button
            with action_cols[0]:
                if st.button("📄 Summarize", key=f"summarize_{url}"):
                    with st.spinner("Generating summary..."):
                        prompt_sum = generate_summary_prompt(snippet, url)
                        generated_summary, error = generate_gemini(prompt_sum)
                        if not error and generated_summary:
                            st.session_state.current_processed_results[url]["summary"] = generated_summary
                            st.rerun()
                        else:
                            st.error(f"Summary failed: {error or 'Unknown error'}")

            # Annotate Button
            with action_cols[1]:
                can_annotate = summary is not None
                if st.button("✍️ Annotate", key=f"annotate_{url}", disabled=not can_annotate):
                    if can_annotate:
                        with st.spinner("Generating annotation..."):
                            prompt_ann = generate_annotation_prompt(title, url, query, summary)
                            generated_annotation, error = generate_gemini(prompt_ann)
                            if not error and generated_annotation:
                                st.session_state.current_processed_results[url]["annotation"] = generated_annotation
                                st.rerun()
                            else:
                                st.error(f"Annotation failed: {error or 'Unknown error'}")

            # Save Button & Folder Selector
            with action_cols[2]:
                save_folder_options = {"root": "All Items (Root)"}
                save_folder_options.update({f.get('id'): f.get('name') for f in get_folders()}) # Get fresh list

                select_key = f"folder_select_{url}"
                selected_save_folder_id = st.selectbox(
                    "Save to:", options=list(save_folder_options.keys()),
                    format_func=lambda x: save_folder_options[x], key=select_key, label_visibility="collapsed"
                )

                if st.button("💾 Save", key=f"save_{url}"):
                    # Use existing summary/annotation if available, otherwise generate on the fly
                    current_summary = result_data.get("summary")
                    current_annotation = result_data.get("annotation")

                    with st.spinner("Saving..."):
                        # Generate summary if needed
                        if not current_summary:
                            st.info("Generating summary before saving...")
                            prompt_sum = generate_summary_prompt(snippet, url)
                            current_summary, error_sum = generate_gemini(prompt_sum)
                            if error_sum or not current_summary:
                                st.error(f"Failed to generate summary for saving: {error_sum}. Item not saved.")
                                continue # Skip saving
                            st.session_state.current_processed_results[url]["summary"] = current_summary

                        # Generate annotation if needed (and summary succeeded)
                        if not current_annotation and current_summary:
                            st.info("Generating annotation before saving...")
                            prompt_ann = generate_annotation_prompt(title, url, query, current_summary)
                            current_annotation, error_ann = generate_gemini(prompt_ann)
                            if error_ann or not current_annotation:
                                st.warning("Failed to generate annotation, saving without it.")
                                current_annotation = "" # Save empty
                            st.session_state.current_processed_results[url]["annotation"] = current_annotation

                        # Save the item
                        saved_item_key = save_library_item(
                            title=title, url=url, query=query, source_type=source_type,
                            folder_id=selected_save_folder_id, summary=current_summary or "",
                            annotation=current_annotation or "", tavily_snippet=snippet
                        )
                        if saved_item_key:
                             # Remove from processing list after successful save
                             del st.session_state.current_processed_results[url]
                             st.rerun() # Refresh UI

            # Display generated content below buttons
            if summary:
                with st.expander("View Generated Summary"):
                    st.markdown(summary)
            if annotation:
                 with st.expander("View Generated Annotation"):
                     st.markdown(annotation)

# --- Display Library Items ---
st.divider()
st.subheader(f" L📂 Library Items in '{folder_options.get(st.session_state.selected_folder_id, 'Unknown Folder')}'")

library_items = get_library_items(st.session_state.selected_folder_id)

if not library_items:
    st.info("No items found for this selection.")
else:
    for item in library_items:
        item_key = item.get('item_key') # Get the full key used for deletion
        if not item_key: continue # Skip if key is missing

        with st.container(border=True):
            st.markdown(f"**[{item.get('title', 'No Title')}]({item.get('url', '#')})**")
            st.caption(f"Added: {item.get('added_timestamp', 'N/A').split('T')[0]} | Source: {item.get('source_type', 'N/A')}")
            st.caption(f"Original Query: _{item.get('query', 'N/A')}_")

            if item.get('summary'):
                with st.expander("Summary"):
                    st.markdown(item['summary'])
            if item.get('annotation'):
                with st.expander("Annotation"):
                    st.markdown(item['annotation'])

            # Delete Button for Library Item
            if st.button("🗑️ Delete Item", key=f"delete_item_{item_key}", help="Permanently delete this item"):
                # Add confirmation checkbox
                if st.checkbox(f"Confirm delete?", key=f"confirm_delete_item_{item_key}", value=False):
                    delete_library_item(item_key)
                    st.rerun() # Refresh the library view
