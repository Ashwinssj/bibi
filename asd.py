import requests
import json

# =============================================================================
# SECTION 1: CONFIGURATION
#
# Define all necessary parameters in this section for easy modification.
# - BASE_URL: The versioned endpoint for the DOAJ API. Using v3 is recommended
#   for long-term stability.
# - API_KEY: Your personal API key from your DOAJ account. While not always
#   mandatory for public searches, it's a best practice to include it.
# - SEARCH_PARAMS: A dictionary containing the query parameters. This allows for
#   clean separation of the query logic from the request execution.
# =============================================================================

BASE_URL = "https://doaj.org/api/search/articles/"
# Replace 'YOUR_API_KEY' with the key from your DOAJ account settings [13]
API_KEY = "ed627ae6984f4ddab905e27632f1ea58" 

# Example Search: Find articles with "CRISPR" in the title or abstract,
# published by "Springer", sorted by the date they were added to DOAJ.
SEARCH_QUERY = 'AI'

SEARCH_PARAMS = {
    "search_query": SEARCH_QUERY,
    "page": 1,
    "pageSize": 100, # Requesting 100 results per page for efficiency
    "sort": "title:asc", # Get the most recently added articles first
}

# =============================================================================
# SECTION 2: REQUEST EXECUTION AND ERROR HANDLING
#
# This section constructs and sends the API request. It includes robust error
# handling to catch common HTTP errors like 404 (Not Found), 403 (Forbidden),
# or 5xx server errors.
# =============================================================================

print(f"Querying DOAJ API with query: {SEARCH_QUERY}")

try:
    # The `requests` library automatically URL-encodes the parameters
    # from the `params` dictionary.
    response = requests.get(BASE_URL, params=SEARCH_PARAMS)

    # This line is crucial for error handling. It will raise an HTTPError
    # if the HTTP request returned an unsuccessful status code (4xx or 5xx).
    response.raise_for_status()

    # If the request was successful, parse the JSON response.
    data = response.json()
    print("Successfully retrieved data from DOAJ API.")

except requests.exceptions.HTTPError as http_err:
    print(f"HTTP error occurred: {http_err}")
    print(f"Status Code: {response.status_code}")
    print(f"Response Body: {response.text}")
    data = None
except requests.exceptions.RequestException as req_err:
    print(f"An error occurred during the request: {req_err}")
    data = None
except json.JSONDecodeError:
    print("Failed to parse JSON from the response.")
    print(f"Response Text: {response.text}")
    data = None

# =============================================================================
# SECTION 3: RESPONSE PROCESSING AND PAGINATION
#
# This section processes the data if the request was successful. It demonstrates
# how to access key information and provides the logic for handling pagination
# to retrieve all results for a given query.
# =============================================================================

if data:
    total_hits = data.get("total", 0)
    print(f"Found a total of {total_hits} articles matching the query.")

    # The actual results are in the 'results' key, which is a list of articles.
    articles = data.get("results",)
    
    print(f"Displaying first {len(articles)} results from page {SEARCH_PARAMS['page']}:")
    print("-" * 40)

    for article in articles:
        # The core metadata is stored in the 'bibjson' object.
        bibjson = article.get("bibjson", {})
        title = bibjson.get("title", "No Title Available")
        
        # Authors are in a list of dictionaries.
        authors = bibjson.get("author",)
        author_names = ", ".join([author.get("name", "N/A") for author in authors])
        
        # Identifiers are also in a list. Find the DOI.
        doi = next((ident['id'] for ident in bibjson.get('identifier',) if ident.get('type') == 'doi'), 'No DOI')
        
        print(f"Title: {title}")
        print(f"Authors: {author_names}")
        print(f"DOI: {doi}")
        print("-" * 20)

    # --- Pagination Logic ---
    # To get all results, you would loop until you have retrieved all pages.
    # The total number of pages can be calculated from 'total' and 'pageSize'.
    if total_hits > 0:
        num_pages = (total_hits + SEARCH_PARAMS - 1) // SEARCH_PARAMS
        print(f"\nPagination: This is page {SEARCH_PARAMS['page']} of {num_pages} total pages.")
        # To fetch the next page, you would increment 'page' in SEARCH_PARAMS
        # and re-run the request in a loop.