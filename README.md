# 📚 Doc Siofbq - AI Research Assistant

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://your-deployed-app-url.onrender.com) <!-- Replace with your actual deployed URL -->

An interactive Django web application designed to assist with research by leveraging the power of AI for web searching, content summarization, annotation generation, and organized storage. It features full user authentication (including Google Sign-in) and per-user data persistence in a PostgreSQL database.

This tool allows authenticated users to:
*   Register and log in (local accounts or Google).
*   Enter research queries.
*   Search the web for relevant sources using Tavily, Google Scholar, Exa.ai, and DOAJ APIs.
*   Generate comprehensive AI-powered research reports from Exa.ai.
*   Generate concise summaries of search results using Google's Gemini model.
*   Create annotated bibliography entries based on the summaries.
*   Generate citations in multiple styles (MLA, APA, Chicago, Harvard, Vancouver).
*   Save interesting findings (URL, title, summary, annotation, original query, and more metadata) to a persistent library.
*   Organize saved items into custom folders.
*   Maintain a chat history for the research session, with history loaded per user.

All user-specific data (folders, library items, chat messages) are stored persistently in a PostgreSQL database. Django's session management will use the database locally, and can optionally use Redis when deployed on Render.

<!-- Add a screenshot or GIF of the application in action here -->
<!-- ![App Screenshot](path/to/screenshot.png) -->

## ✨ Features

*   **User Authentication:** Secure login and registration with `django.contrib.auth` and `django-allauth`.
*   **Google Sign-in:** Seamless authentication via Google OAuth.
*   **Per-User Data:** Chats, folders, and library items are strictly isolated and accessible only by the owning user.
*   **AI-Powered Unified Search:** Integrates with Tavily, Google Scholar, Exa.ai, and DOAJ for comprehensive academic web searches.
*   **AI Research Report:** Generates a synthesized research report for a given query using Exa.ai's research task API.
*   **AI Summarization:** Uses Google Gemini (gemini-1.5-flash) to generate structured summaries of web content.
*   **AI Annotation:** Generates annotated bibliography entries based on generated summaries and source details.
*   **Citation Generation:** Provides citations in MLA, APA, Chicago, Harvard, and Vancouver styles.
*   **Persistent Library:** Saves research items (links, summaries, annotations, and detailed metadata) to a PostgreSQL database.
*   **Folder Organization:** Allows users to create and manage custom folders to categorize saved library items.
*   **Chat History:** Stores and displays conversational chat history for each user.
*   **Secure Configuration:** Uses environment variables for API keys and database credentials.

## 🛠️ Technologies Used

*   **Backend Framework:** [Django](https://www.djangoproject.com/)
*   **Language:** Python 3
*   **AI Model:** [Google Gemini API](https://ai.google.dev/) (specifically `gemini-1.5-flash`)
*   **Web Search:** [Tavily Search API](https://tavily.com/), [SerpApi (Google Scholar)](https://serpapi.com/), [Exa.ai](https://exa.ai/), [DOAJ API](https://doaj.org/api/v2/search)
*   **Web Scraping:** [ScraperAPI](https://www.scraperapi.com/)
*   **Authentication:** [Django Allauth](https://django-allauth.readthedocs.io/) (with Google Social Account provider)
*   **Database:** [PostgreSQL](https://www.postgresql.org/) (locally via `psycopg[binary]`, managed with tools like [pgAdmin](https://www.pgadmin.org/))
*   **Session/Cache (Optional for Deployment):** [Redis](https://redis.io/) (via `django-redis` for Render)
*   **Static Files:** [WhiteNoise](http://whitenoise.evans.io/en/stable/)
*   **Deployment:** Configured for [Render](https://render.com/)

## ⚙️ Setup and Installation (Local)

Follow these steps to run the application locally:

1.  **Prerequisites:**
    *   Python 3.11 or higher installed.
    *   Git installed.
    *   **PostgreSQL database server running locally.** You can install it directly or use Docker.
    *   **pgAdmin (optional, but recommended)** for managing your local PostgreSQL database.
    *   Access to Google Gemini API, Tavily Search API, SerpApi, Exa.ai, and ScraperAPI.
    *   Google Cloud Project for Google OAuth 2.0 Client ID/Secret.

2.  **Clone the Repository:**
    ```bash
    git clone https://github.com/your-username/your-repo-name.git # Replace with your actual repo
    cd your-repo-name
