import uuid
import json
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from . import services # Import your services module

# Initialize clients (will be called on first import, handles single instance)
try:
    services.configure_clients()
except Exception as e:
    # Log the error, but don't stop the server from starting if possible
    print(f"Error configuring services on startup: {e}")

def process_query_and_respond(request, query_text, session_id):
    """Helper function to encapsulate query processing and response generation."""
    messages.info(request, "🧠 Thinking... Searching across multiple sources (Tavily, Google Scholar, Exa.ai, DOAJ) and generating a research report...")
    
    combined_results, exa_research_report, search_errors = services.perform_unified_search(query_text)
    
    for err in search_errors:
        messages.warning(request, err)

    # Store the query for the main heading
    request.session['last_search_query'] = query_text
    
    # Store the Exa.ai research report separately
    request.session['last_exa_report'] = exa_research_report if exa_research_report and exa_research_report.strip() != "No report content generated." else None

    if combined_results:
        request.session['current_processed_results'] = {res['url']: res for res in combined_results}
        assistant_chat_message = f"Found {len(combined_results)} potential sources for '{query_text}'. Please see the results below."
    else:
        request.session['current_processed_results'] = {}
        assistant_chat_message = f"😕 Sorry, I couldn't find specific individual results for '{query_text}' from any source."

    # Append a concise assistant message to the chat history
    request.session['messages'].append({"role": "assistant", "content": assistant_chat_message})
    request.session.modified = True
    save_err = services.save_chat_message(session_id, "assistant", assistant_chat_message)
    if save_err:
        messages.error(request, f"Error saving chat message: {save_err}")


def home_view(request):
    # Initialize session_id if not present
    if 'session_id' not in request.session:
        request.session['session_id'] = str(uuid.uuid4())
        request.session.modified = True
    
    # Clear the chat display in the session when visiting the home page via GET
    # This ensures the chat window appears empty on initial visit or navigation back to home.
    if request.method == 'GET':
        if 'messages' in request.session:
            del request.session['messages']
            request.session.modified = True
        if 'current_processed_results' in request.session:
            del request.session['current_processed_results']
            request.session.modified = True
        if 'show_citations_search' in request.session:
            del request.session['show_citations_search']
            request.session.modified = True
        # Clear new session variables for a clean start
        if 'last_search_query' in request.session:
            del request.session['last_search_query']
            request.session.modified = True
        if 'last_exa_report' in request.session:
            del request.session['last_exa_report']
            request.session.modified = True
    
    if request.method == 'POST':
        initial_query = request.POST.get('initial_query')
        if initial_query:
            # Add user message to session and Redis
            # Ensure messages list is initialized before appending
            request.session.setdefault('messages', [])
            request.session['messages'].append({"role": "user", "content": initial_query})
            
            # Set a flag to indicate that chat_view needs to process this query
            request.session['just_submitted_initial_query'] = True 
            request.session.modified = True
            
            save_err = services.save_chat_message(request.session['session_id'], "user", initial_query)
            if save_err:
                messages.error(request, f"Error saving chat message: {save_err}")
            
            # Redirect to chat view to process the query
            return redirect('research_assistant:chat')
    
    return render(request, 'research_assistant/home.html')

def chat_view(request):
    session_id = request.session.get('session_id', str(uuid.uuid4()))
    request.session['session_id'] = session_id

    # Load messages from Redis if not already in session.
    # This ensures that if the user navigates directly to /research/chat/
    # or if the session was cleared from home_view, the history is loaded.
    if 'messages' not in request.session:
        loaded_messages, load_err = services.load_chat_history(session_id)
        if load_err:
            messages.error(request, f"Error loading chat history: {load_err}")
        request.session['messages'] = loaded_messages
        request.session.modified = True
    
    messages_in_session = request.session.get('messages', [])

    if request.method == 'POST':
        prompt = request.POST.get('prompt')
        if prompt:
            request.session['messages'].append({"role": "user", "content": prompt})
            request.session.modified = True
            save_err = services.save_chat_message(session_id, "user", prompt)
            if save_err:
                messages.error(request, f"Error saving chat message: {save_err}")
            
            # After a POST, we always process the query
            process_query_and_respond(request, prompt, session_id)
            return redirect('research_assistant:chat') # Redirect to prevent re-submission on refresh

    elif request.method == 'GET':
        # Check if this GET request is a result of an initial submission from home_view
        # The .pop() method removes the flag after checking it, ensuring it's processed only once.
        if request.session.pop('just_submitted_initial_query', False):
            # The last message in session should be the initial query from the user
            if messages_in_session and messages_in_session[-1]['role'] == 'user':
                prompt_to_process = messages_in_session[-1]['content']
                process_query_and_respond(request, prompt_to_process, session_id)
                # After processing, redirect again to clear the flag and ensure PRG pattern
                return redirect('research_assistant:chat')

    # Existing context setup for rendering
    current_processed_results = request.session.get('current_processed_results', {})
    folders, folder_err = services.get_folders()
    if folder_err:
        messages.error(request, f"Error retrieving folders: {folder_err}")
        folders = []

    selected_folder_id = request.session.get('selected_folder_id')
    selected_folder_data = None
    if selected_folder_id and selected_folder_id != "root" and selected_folder_id != "None":
        selected_folder_data = next((f for f in folders if f['id'] == selected_folder_id), None)

    context = {
        # 'messages': messages_in_session, # No longer needed for main chat display
        'current_processed_results': current_processed_results.values(),
        'selected_folder_id': selected_folder_id,
        'selected_folder_data': selected_folder_data,
        'show_citations_search': request.session.get('show_citations_search', {}),
        'folders': folders,
        'messages_history': request.session.get('messages', []), # This is for the sidebar chat history
        'last_search_query': request.session.get('last_search_query'), # Pass the last search query
        'last_exa_report': request.session.get('last_exa_report'), # Pass the last Exa.ai report
    }
    return render(request, 'research_assistant/chat.html', context)

def library_view(request):
    session_id = request.session.get('session_id', str(uuid.uuid4()))
    request.session['session_id'] = session_id

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'select_folder':
            selected_folder_id = request.POST.get('folder_selector_radio')
            if selected_folder_id == "None":
                request.session['selected_folder_id'] = None
            else:
                request.session['selected_folder_id'] = selected_folder_id
            request.session.modified = True
            return redirect('research_assistant:library')

        elif action == 'create_folder':
            folder_name = request.POST.get('new_folder_name_input')
            folder_id, err = services.create_folder(folder_name)
            if err:
                messages.error(request, f"Error creating folder: {err}")
            else:
                messages.success(request, f"Folder '{folder_name}' created.")
                request.session['selected_folder_id'] = folder_id
                request.session.modified = True
            return redirect('research_assistant:library')

        elif action == 'delete_folder':
            folder_id_to_delete = request.POST.get('folder_id_to_delete')
            if folder_id_to_delete:
                folder_key_to_delete = f"{services.FOLDER_PREFIX}{folder_id_to_delete}"
                delete_err = services.delete_folder(folder_key_to_delete)
                if delete_err:
                    messages.error(request, f"Error deleting folder: {delete_err}")
                else:
                    messages.success(request, "Folder deleted successfully.")
                    if request.session.get('selected_folder_id') == folder_id_to_delete:
                        request.session['selected_folder_id'] = None
                    request.session.modified = True
            return redirect('research_assistant:library')

        elif action == 'cite_library':
            item_key = request.POST.get('item_key')
            library_items, _ = services.get_library_items(request.session.get('selected_folder_id', 'root'))
            item = next((i for i in library_items if i.get('item_key') == item_key), None)
            if item:
                citations = services.generate_citations(item)
                show_citations_lib = request.session.get('show_citations_lib', {})
                show_citations_lib[item_key] = citations
                request.session['show_citations_lib'] = show_citations_lib
                request.session.modified = True
            else:
                messages.error(request, "Library item not found for citation.")
            return redirect('research_assistant:library')

        elif action == 'close_cite_library':
            item_key = request.POST.get('item_key')
            show_citations_lib = request.session.get('show_citations_lib', {})
            if item_key in show_citations_lib:
                del show_citations_lib[item_key]
                request.session['show_citations_lib'] = show_citations_lib
                request.session.modified = True
            return redirect('research_assistant:library')

        elif action == 'delete_library_item':
            messages.error(request, "Invalid delete action for library item.")
            return redirect('research_assistant:library')

    folders, folder_err = services.get_folders()
    if folder_err:
        messages.error(request, f"Error retrieving folders: {folder_err}")
        folders = []

    folder_options = {"None": "Select a folder to view..."}
    folder_options.update({"root": "All Items (Root)"})
    folder_options.update({f["id"]: f["name"] for f in folders})

    selected_folder_id = request.session.get('selected_folder_id')
    selected_folder_data = None # Initialize to None
    if selected_folder_id and selected_folder_id != "root" and selected_folder_id != "None":
        selected_folder_data = next((f for f in folders if f['id'] == selected_folder_id), None)


    library_items = []
    if selected_folder_id is not None and selected_folder_id != "None":
        library_items, items_err = services.get_library_items(selected_folder_id)
        if items_err:
            messages.error(request, f"Error retrieving library items: {items_err}")
            library_items = []

    context = {
        'folders': folders,
        'folder_options': folder_options,
        'selected_folder_id': selected_folder_id,
        'selected_folder_data': selected_folder_data, # Pass selected folder data directly
        'library_items': library_items,
        'show_citations_lib': request.session.get('show_citations_lib', {}),
        'messages_history': request.session.get('messages', []),
    }
    return render(request, 'research_assistant/library.html', context)

def create_folder_view(request):
    if request.method == 'POST':
        folder_name = request.POST.get('new_folder_name_input')
        folder_id, err = services.create_folder(folder_name)
        if err:
            messages.error(request, f"Error creating folder: {err}")
        else:
            messages.success(request, f"Folder '{folder_name}' created.")
            request.session['selected_folder_id'] = folder_id # Auto-select new folder
            request.session.modified = True
        return redirect('research_assistant:library')
    return redirect('research_assistant:library') # Should not be accessed directly via GET

def delete_folder_view(request, folder_id):
    if request.method == 'POST':
        folder_key = f"{services.FOLDER_PREFIX}{folder_id}"
        err = services.delete_folder(folder_key)
        if err:
            messages.error(request, f"Error deleting folder: {err}")
        else:
            messages.success(request, "Folder deleted.")
            if request.session.get('selected_folder_id') == folder_id:
                request.session['selected_folder_id'] = None
            request.session.modified = True
        return redirect('research_assistant:library')
    return redirect('research_assistant:library')

def process_result_view(request, url_id):
    """Handles summarize, annotate, and cite actions for search results."""
    current_processed_results = request.session.get('current_processed_results', {})
    # URL_id might contain slashes, so it's the key
    result_data = current_processed_results.get(url_id)

    if not result_data:
        messages.error(request, "Result not found or session expired.")
        return redirect('research_assistant:chat')

    action = request.POST.get('action')
    url = result_data['url'] # Use original URL for scraping

    if action == 'summarize':
        is_journal_entry = (result_data.get('source_type') == "DOAJ Journal")
        if is_journal_entry:
            messages.warning(request, "Summarization is not applicable for journal entries.")
            return redirect('research_assistant:chat')

        messages.info(request, "Preparing content for summary...")
        text_for_summary = result_data["content_snippet"]
        scraped_content = None
        
        urls_to_try = []
        if result_data.get('pdf_url'):
            urls_to_try.append({"url": result_data['pdf_url'], "type": "pdf"})
        if result_data.get('main_pub_url') and result_data['main_pub_url'] != result_data.get('pdf_url'):
            urls_to_try.append({"url": result_data['main_pub_url'], "type": "html"})
        if url and url != result_data.get('pdf_url') and url != result_data.get('main_pub_url'):
            urls_to_try.append({"url": url, "type": "html"})
        if url and ".pdf" in url.lower() and not any(d['url'] == url and d['type'] == 'html' for d in urls_to_try):
            urls_to_try.append({"url": url, "type": "html"})

        for attempt in urls_to_try:
            temp_scraped_content, temp_scrape_error = services.scrape_article_content(attempt["url"])
            if temp_scraped_content and len(temp_scraped_content) > 200:
                scraped_content = temp_scraped_content
                text_for_summary = scraped_content
                break
            else:
                messages.warning(request, f"Scraping attempt for {attempt['url']} failed: {temp_scrape_error}")

        if not scraped_content or len(text_for_summary) < 200:
            text_for_summary = result_data["content_snippet"]
            if not text_for_summary:
                messages.error(request, "No content available to summarize (PDF, HTML, or snippet failed/empty).")
                return redirect('research_assistant:chat')
            messages.info(request, "Using snippet for summary as full content could not be scraped.")

        prompt_structured_sum = services.generate_structured_summary_prompt(
            title=result_data['title'],
            authors=result_data.get('authors', ''),
            year=result_data.get('year', ''),
            journal_name=result_data.get('journal_name', ''),
            doi=result_data.get('doi', ''),
            content_to_summarize=text_for_summary,
            url=url
        )
        generated_summary, error_structured = services.generate_gemini(prompt_structured_sum)

        if not error_structured and generated_summary:
            current_processed_results[url_id]["summary"] = generated_summary
            messages.success(request, "Summary generated successfully.")
        else:
            messages.error(request, f"Summary generation failed: {error_structured or 'Unknown API error'}. Please check content or try again.")
        
        request.session['current_processed_results'] = current_processed_results
        request.session.modified = True
        return redirect('research_assistant:chat')

    elif action == 'annotate':
        is_journal_entry = (result_data.get('source_type') == "DOAJ Journal")
        if is_journal_entry:
            messages.warning(request, "Annotation is not applicable for journal entries.")
            return redirect('research_assistant:chat')
        
        if not result_data.get('summary'):
            messages.warning(request, "Please generate a summary first before annotating.")
            return redirect('research_assistant:chat')
        
        messages.info(request, "Generating annotation...")
        prompt_ann = services.generate_annotation_prompt(
            result_data['title'], url, result_data['optimized_query'], 
            result_data['summary'], result_data.get('authors', ''), result_data.get('year', '')
        )
        generated_annotation, error = services.generate_gemini(prompt_ann)
        if not error and generated_annotation:
            current_processed_results[url_id]["annotation"] = generated_annotation
            messages.success(request, "Annotation generated successfully.")
        else:
            messages.error(request, f"Annotation failed: {error or 'Unknown error'}")
        
        request.session['current_processed_results'] = current_processed_results
        request.session.modified = True
        return redirect('research_assistant:chat')

    elif action == 'cite':
        citations = services.generate_citations(result_data)
        # Store citations in session to display in the template
        show_citations_search = request.session.get('show_citations_search', {})
        show_citations_search[url_id] = citations
        request.session['show_citations_search'] = show_citations_search
        request.session.modified = True
        return redirect('research_assistant:chat')
    
    elif action == 'close_cite_search':
        show_citations_search = request.session.get('show_citations_search', {})
        if url_id in show_citations_search:
            del show_citations_search[url_id]
            request.session['show_citations_search'] = show_citations_search
            request.session.modified = True
        return redirect('research_assistant:chat')

    messages.error(request, "Invalid action.")
    return redirect('research_assistant:chat')

def save_item_view(request, url_id):
    if request.method == 'POST':
        current_processed_results = request.session.get('current_processed_results', {})
        result_data = current_processed_results.get(url_id)

        if not result_data:
            messages.error(request, "Item to save not found or session expired.")
            return redirect('research_assistant:chat')

        selected_save_folder_id = request.POST.get('save_to_folder_id', 'root')

        title = result_data["title"]
        url = result_data["url"]
        query = result_data["query"]
        source_type = result_data["source_type"]
        content_snippet = result_data["content_snippet"]
        authors = result_data.get("authors", "")
        year = result_data.get("year", "")
        pdf_url = result_data.get("pdf_url", "")
        main_pub_url = result_data.get("main_pub_url", "")
        doi = result_data.get("doi", "")
        journal_name = result_data.get("journal_name", "")
        volume = result_data.get("volume", "")
        pages = result_data.get("pages", "")
        publisher = result_data.get("publisher", "")
        issn = result_data.get("issn", "")

        current_summary = result_data.get("summary")
        current_annotation = result_data.get("annotation")
        is_journal_entry = (source_type == "DOAJ Journal")

        # Auto-generate summary if not present and not a journal entry
        if not current_summary and not is_journal_entry:
            messages.info(request, "Generating summary before saving...")
            text_to_summarize_for_save = content_snippet
            scraped_content_for_save = None

            urls_to_try_for_save = []
            if pdf_url: urls_to_try_for_save.append({"url": pdf_url, "type": "pdf"})
            if main_pub_url and main_pub_url != pdf_url: urls_to_try_for_save.append({"url": main_pub_url, "type": "html"})
            if url and url != pdf_url and url != main_pub_url: urls_to_try_for_save.append({"url": url, "type": "html"})
            if url and ".pdf" in url.lower() and not any(d['url'] == url and d['type'] == 'html' for d in urls_to_try_for_save):
                urls_to_try_for_save.append({"url": url, "type": "html"})

            for attempt in urls_to_try_for_save:
                temp_scraped_content, temp_scrape_error = services.scrape_article_content(attempt["url"])
                if temp_scraped_content and len(temp_scraped_content) > 200:
                    scraped_content_for_save = temp_scraped_content
                    text_to_summarize_for_save = scraped_content_for_save
                    break
            
            if not scraped_content_for_save or len(text_to_summarize_for_save) < 200:
                text_to_summarize_for_save = content_snippet
                if not text_to_summarize_for_save and not is_journal_entry:
                    messages.error(request, "No content available to summarize for saving. Item not saved.")
                    return redirect('research_assistant:chat')
                elif not is_journal_entry:
                    messages.info(request, "Using snippet for summary as full content could not be scraped for saving.")
            
            if not is_journal_entry:
                prompt_structured_sum = services.generate_structured_summary_prompt(
                    title=title, authors=authors, year=year,
                    journal_name=journal_name, doi=doi,
                    content_to_summarize=text_to_summarize_for_save,
                    url=url
                )
                current_summary, error_structured_save = services.generate_gemini(prompt_structured_sum)
                if error_structured_save or not current_summary:
                    messages.warning(request, f"Summary generation failed for saving: {error_structured_save}. Saving without summary.")
                    current_summary = ""
                result_data["summary"] = current_summary # Update in session for potential future use

        # Auto-generate annotation if not present and summary exists (and not a journal entry)
        if not current_annotation and current_summary and not is_journal_entry:
            messages.info(request, "Generating annotation before saving...")
            prompt_ann = services.generate_annotation_prompt(
                title, url, query, current_summary, authors, year
            )
            current_annotation, error_ann = services.generate_gemini(prompt_ann)
            if error_ann or not current_annotation:
                messages.warning(request, "Failed to generate annotation, saving without it.")
                current_annotation = ""
            result_data["annotation"] = current_annotation # Update in session

        if is_journal_entry:
            current_summary = ""
            current_annotation = ""

        saved_item_key, err = services.save_library_item(
            title=title, url=url, query=query, source_type=source_type,
            folder_id=selected_save_folder_id, summary=current_summary or "",
            annotation=current_annotation or "", content_snippet=content_snippet,
            authors=authors, year=year,
            pdf_url=pdf_url, main_pub_url=main_pub_url,
            doi=doi, journal_name=journal_name, volume=volume, pages=pages,
            publisher=publisher, issn=issn
        )
        if err:
            messages.error(request, f"Error saving item: {err}")
        else:
            messages.success(request, f"Item '{title[:30]}...' saved.")
            # Remove from current_processed_results after saving
            if url_id in current_processed_results:
                del current_processed_results[url_id]
                request.session['current_processed_results'] = current_processed_results
                request.session.modified = True
        return redirect('research_assistant:chat')
    return redirect('research_assistant:chat')

def delete_library_item_view(request, item_key):
    if request.method == 'POST':
        err = services.delete_library_item(item_key)
        if err:
            messages.error(request, f"Error deleting item: {err}")
        else:
            messages.success(request, "Item deleted.")
        return redirect('research_assistant:library')
    return redirect('research_assistant:library')

def start_new_research_session_view(request):
    # Clear session data relevant to the current research
    if 'messages' in request.session:
        del request.session['messages']
    if 'current_processed_results' in request.session:
        del request.session['current_processed_results']
    if 'show_citations_search' in request.session:
        del request.session['show_citations_search']
    if 'just_submitted_initial_query' in request.session: # Clear this flag too
        del request.session['just_submitted_initial_query']
    if 'last_search_query' in request.session: # Clear the last search query
        del request.session['last_search_query']
    if 'last_exa_report' in request.session: # Clear the last Exa.ai report
        del request.session['last_exa_report']
    # Do NOT delete session_id, it identifies the user's chat history in Redis
    # The chat history in Redis is persistent, but the display in the session is cleared.
    request.session.modified = True
    messages.info(request, "Started a new research session. Chat display cleared.")
    return redirect('research_assistant:home')

def clear_chat_display_view(request):
    if 'messages' in request.session:
        del request.session['messages']
        request.session.modified = True
    if 'current_processed_results' in request.session:
        del request.session['current_processed_results']
        request.session.modified = True
    if 'show_citations_search' in request.session:
        del request.session['show_citations_search']
        request.session.modified = True
    if 'last_search_query' in request.session: # Clear the last search query
        del request.session['last_search_query']
    if 'last_exa_report' in request.session: # Clear the last Exa.ai report
        del request.session['last_exa_report']
    messages.info(request, "Chat display cleared.")
    return redirect('research_assistant:chat')
