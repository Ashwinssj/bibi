# 📚 Doc Siofbq - AI Research Assistant

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://your-deployed-app-url.onrender.com) <!-- Replace with your actual deployed URL -->

An interactive Streamlit application designed to assist with research by leveraging the power of AI for web searching, content summarization, annotation generation, and organized storage.

This tool allows users to:
*   Enter research queries.
*   Search the web for relevant sources using the Tavily Search API.
*   Generate concise summaries of search results using Google's Gemini model.
*   Create annotated bibliography entries based on the summaries.
*   Save interesting findings (URL, title, summary, annotation, original query) to a persistent library.
*   Organize saved items into custom folders.
*   Maintain a chat history for the research session.

All library data and chat history are stored persistently using Redis (specifically configured for Upstash).

<!-- Add a screenshot or GIF of the application in action here -->
<!-- ![App Screenshot](path/to/screenshot.png) -->

## ✨ Features

*   **AI-Powered Search:** Integrates with Tavily for efficient web searches based on user queries.
*   **AI Summarization:** Uses Google Gemini (gemini-1.5-flash) to generate brief summaries of web content snippets.
*   **AI Annotation:** Generates annotated bibliography entries based on generated summaries and source details.
*   **Persistent Library:** Saves research items (links, summaries, annotations) to a Redis database (Upstash).
*   **Folder Organization:** Allows users to create and manage folders to categorize saved library items.
*   **Interactive Chat Interface:** Uses Streamlit's chat elements for a conversational user experience.
*   **Session History:** Stores and displays the chat history for the current session (backed by Redis).
*   **Secure Configuration:** Uses Streamlit Secrets or Environment Variables for API keys and database credentials.

## 🛠️ Technologies Used

*   **Frontend:** [Streamlit](https://streamlit.io/)
*   **Language:** Python 3
*   **AI Model:** [Google Gemini API](https://ai.google.dev/) (specifically `gemini-1.5-flash`)
*   **Web Search:** [Tavily Search API](https://tavily.com/)
*   **Database:** [Redis](https://redis.io/) (hosted on [Upstash](https://upstash.com/))
*   **Deployment:** Configured for [Render](https://render.com/) (or local execution)

## ⚙️ Setup and Installation (Local)

Follow these steps to run the application locally:

1.  **Prerequisites:**
    *   Python 3.8 or higher installed.
    *   Git installed.
    *   Access to Google Gemini API, Tavily Search API, and an Upstash Redis instance.

2.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd <repository-directory>
Create a Virtual Environment (Recommended):
# Linux/macOS
 python -m venv .venv
source .venv/bin/activate

# Windows
python -m venv .venv
.\.venv\Scripts\activateInstall Dependencies:

