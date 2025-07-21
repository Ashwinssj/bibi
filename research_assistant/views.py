# research_assistant/views.py
import uuid
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User # Import User model
from . import services # Import your services module
from .models import Folder, LibraryItem, ChatMessage # Import your new models

# Initialize clients (will be called on first import, handles single instance)
services.configure_clients()

def process_query_and_respond(request, query_text, user):
    """Helper function to encapsulate query processing and response generation."""
    messages.info(request, "🧠 Thinking... Searching across multiple sources (Tavily, Google Scholar, Exa.ai, DOAJ) and generating a research report...")
    
    combined_results, exa_research_report, search_errors = services.perform_unified_search(query_text)
    
    for err in search_errors:
        messages.warning(request, err)

    # Store the query for the main heading in session (temporary display)
    request.session['last_search_query'] = query_text
    
    # Store the Exa.ai research report separately in session (temporary display)
    request.session['last_exa_report'] = exa_research_report if exa_research_report and exa_research_report.strip() != "No report content generated." else None

    if combined_results:
        # Store results in session by URL for processing actions
        request.session['current_processed_results'] = {res['url']: res for res in combined_results}
        assistant_chat_message = f"Found {len(combined_results)} potential sources for '{query_text}'. Please see the results below."
    else:
        request.session['current_processed_results'] = {}
        assistant_chat_message = f"😕 Sorry, I couldn't find specific individual results for '{query_text}' from any source."

    # Append a concise assistant message to the chat history in the database
    ChatMessage.objects.create(user=user, role="assistant", content=assistant_chat_message)
    request.session.modified = True # Ensure session is saved if any session data was updated

def landing_page_view(request):
    """
    Public landing page. Redirects authenticated users to their research home.
    """
    if request.user.is_authenticated:
        return redirect('research_assistant:home')
    return render(request, 'landing_page.html') # This template needs to be created

@login_required
def home_view(request):
    # This view is now for authenticated users only, acting as their main "dashboard"
    # It will display the initial search bar.
    
    # Clear the current display in session when visiting the home page via GET
    # This ensures a clean slate for the main content area, but sidebar history remains.
    if request.method == 'GET':
        if 'messages_display' in request.session: # Renamed from 'messages' to avoid conflict with Django messages
            del request.session['messages_display']
            request.session.modified = True
        if 'current_processed_results' in request.session:
            del request.session['current_processed_results']
            request.session.modified = True
        if 'show_citations_search' in request.session:
            del request.session['show_citations_search']
            request.session.modified = True
        if 'last_search_query' in request.session:
            del request.session['last_search_query']
            request.session.modified = True
        if 'last_exa_report' in request.session:
            del request.session['last_exa_report']
            request.session.modified = True
    
    if request.method == 'POST':
        initial_query = request.POST.get('initial_query')
        if initial_query:
            # Add user message to database
            ChatMessage.objects.create(user=request.user, role="user", content=initial_query)
            
            # Set a flag to indicate that chat_view needs to process this query
            request.session['just_submitted_initial_query'] = True 
            request.session.modified = True
            
            # Redirect to chat view to process the query
            return redirect('research_assistant:chat')
    
    # Context for sidebar (folders and chat history)
    # Ensure folders and chat history are filtered by the current user
    folders = Folder.objects.filter(user=request.user).order_by('name')
    selected_folder_id = request.session.get('selected_folder_id')
    selected_folder_data = None
    if selected_folder_id and selected_folder_id != "root" and selected_folder_id != "None":
        selected_folder_data = folders.filter(id=selected_folder_id).first()

    # Load recent chat history for sidebar display (always from DB)
    messages_history = ChatMessage.objects.filter(user=request.user).order_by('-timestamp')[:100] # Last 100 messages

    context = {
        'folders': folders,
        'selected_folder_id': selected_folder_id,
        'selected_folder_data': selected_folder_data,
        'messages_history': messages_history, # This is for the sidebar chat history
    }
    return render(request, 'research_assistant/home.html', context)

@login_required
def chat_view(request):
    # Load messages for the current display from session, or from DB if new session
    messages_display = request.session.get('messages_display', [])

    if request.method == 'POST':
        prompt = request.POST.get('prompt')
        if prompt:
            # Add user message to database
            ChatMessage.objects.create(user=request.user, role="user", content=prompt)
            # Add to session display for immediate rendering
            messages_display.append({"role": "user", "content": prompt})
            request.session['messages_display'] = messages_display
            request.session.modified = True
            
            # Process the query and respond
            process_query_and_respond(request, prompt, request.user)
            return redirect('research_assistant:chat') # Redirect to prevent re-submission on refresh

    elif request.method == 'GET':
        # If this GET request is a result of an initial submission from home_view
        if request.session.pop('just_submitted_initial_query', False):
            # The last message in DB should be the initial query from the user
            last_user_message = ChatMessage.objects.filter(user=request.user, role='user').order_by('-timestamp').first()
            if last_user_message:
                prompt_to_process = last_user_message.content
                # Add it to the current display
                messages_display.append({"role": "user", "content": prompt_to_process})
                request.session['messages_display'] = messages_display
                request.session.modified = True
                process_query_and_respond(request, prompt_to_process, request.user)
                # After processing, redirect again to clear the flag and ensure PRG pattern
                return redirect('research_assistant:chat')
        
        # If no messages in session display, load from DB for initial chat view
        if not messages_display:
            # Fetch recent messages for display in the main chat window
            messages_display = list(ChatMessage.objects.filter(user=request.user).order_by('timestamp')[:100].values('role', 'content'))
            request.session['messages_display'] = messages_display
            request.session.modified = True

    # Context setup for rendering
    current_processed_results = request.session.get('current_processed_results', {})
    folders = Folder.objects.filter(user=request.user).order_by('name')
    
    selected_folder_id = request.session.get('selected_folder_id')
    selected_folder_data = None
    if selected_folder_id and selected_folder_id != "root" and selected_folder_id != "None":
        selected_folder_data = folders.filter(id=selected_folder_id).first()

    # Load recent chat history for sidebar display (always from DB)
    messages_history = ChatMessage.objects.filter(user=request.user).order_by('-timestamp')[:100]

    context = {
        'current_processed_results': current_processed_results.values(),
        'selected_folder_id': selected_folder_id,
        'selected_folder_data': selected_folder_data,
        'show_citations_search': request.session.get('show_citations_search', {}),
        'folders': folders,
        'messages_history': messages_history, # This is for the sidebar chat history
        'last_search_query': request.session.get('last_search_query'),
        'last_exa_report': request.session.get('last_exa_report'),
        'messages_display': messages_display, # This is for the main chat window display
    }
    return render(request, 'research_assistant/chat.html', context)

@login_required
def library_view(request):
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

        elif action == 'cite_library':
            item_id = request.POST.get('item_id') # Changed from item_key to item_id (UUID)
            item = get_object_or_404(LibraryItem, user=request.user, id=item_id)
            
            # Prepare item data for citation generation (services.py expects a dict)
            item_data_for_citation = {
                "title": item.title,
                "authors": item.authors,
                "year": item.year,
                "journal_name": item.journal_name,
                "volume": item.volume,
                "pages": item.pages,
                "doi": item.doi,
                "url": item.url,
            }
            citations = services.generate_citations(item_data_for_citation)
            show_citations_lib = request.session.get('show_citations_lib', {})
            show_citations_lib[str(item.id)] = citations # Store by string UUID
            request.session['show_citations_lib'] = show_citations_lib
            request.session.modified = True
            return redirect('research_assistant:library')

        elif action == 'close_cite_library':
            item_id = request.POST.get('item_id') # Changed from item_key to item_id (UUID)
            show_citations_lib = request.session.get('show_citations_lib', {})
            if item_id in show_citations_lib:
                del show_citations_lib[item_id]
                request.session['show_citations_lib'] = show_citations_lib
                request.session.modified = True
            return redirect('research_assistant:library')

        # The 'delete_library_item' action is handled by a separate URL/view now.
        # This prevents accidental deletion from this view.
        messages.error(request, "Invalid action for library view.")
        return redirect('research_assistant:library')

    # Fetch folders for the current user
    folders = Folder.objects.filter(user=request.user).order_by('name')

    # Prepare folder options for the radio button group
    folder_options = {"None": "Select a folder to view..."}
    folder_options.update({"root": "All Items (Root)"})
    folder_options.update({str(f.id): f.name for f in folders}) # Convert UUID to string for select box

    selected_folder_id = request.session.get('selected_folder_id')
    selected_folder_data = None
    library_items = []

    # Filter library items based on selected folder
    if selected_folder_id == "root":
        # Items in 'root' are those with no folder assigned (folder__isnull=True)
        library_items = LibraryItem.objects.filter(user=request.user, folder__isnull=True).order_by('-added_timestamp')
    elif selected_folder_id and selected_folder_id != "None":
        # Fetch the specific folder object
        selected_folder_data = folders.filter(id=selected_folder_id).first()
        if selected_folder_data:
            # Filter items by user and selected folder
            library_items = LibraryItem.objects.filter(user=request.user, folder=selected_folder_data).order_by('-added_timestamp')
        else:
            messages.warning(request, "Selected folder not found. Displaying all items in root.")
            request.session['selected_folder_id'] = None # Reset selection if folder not found
            request.session.modified = True
            library_items = LibraryItem.objects.filter(user=request.user, folder__isnull=True).order_by('-added_timestamp')


    # Load recent chat history for sidebar display
    messages_history = ChatMessage.objects.filter(user=request.user).order_by('-timestamp')[:100]

    context = {
        'folders': folders,
        'folder_options': folder_options,
        'selected_folder_id': selected_folder_id,
        'selected_folder_data': selected_folder_data, # Pass selected folder data directly
        'library_items': library_items,
        'show_citations_lib': request.session.get('show_citations_lib', {}),
        'messages_history': messages_history,
    }
    return render(request, 'research_assistant/library.html', context)

@login_required
def create_folder_view(request):
    if request.method == 'POST':
        folder_name = request.POST.get('new_folder_name_input')
        if folder_name:
            try:
                folder = Folder.objects.create(user=request.user, name=folder_name)
                messages.success(request, f"Folder '{folder_name}' created.")
                request.session['selected_folder_id'] = str(folder.id) # Auto-select new folder
                request.session.modified = True
            except Exception as e:
                messages.error(request, f"Error creating folder: {e}. A folder with this name might already exist.")
        else:
            messages.error(request, "Folder name cannot be empty.")
        return redirect('research_assistant:library')
    return redirect('research_assistant:library') # Should not be accessed directly via GET

@login_required
def delete_folder_view(request, folder_id):
    if request.method == 'POST':
        try:
            # Ensure the folder belongs to the current user
            folder = get_object_or_404(Folder, user=request.user, id=folder_id)
            folder_name = folder.name
            folder.delete() # CASCADE delete will handle associated LibraryItems (setting folder_id to NULL)
            messages.success(request, f"Folder '{folder_name}' and its associated items moved to 'All Items (Root)'.")
            # If the deleted folder was currently selected, reset the selection
            if request.session.get('selected_folder_id') == str(folder_id):
                request.session['selected_folder_id'] = None
            request.session.modified = True
        except Exception as e:
            messages.error(request, f"Error deleting folder: {e}")
        return redirect('research_assistant:library')
    return redirect('research_assistant:library')

@login_required
def process_result_view(request, url_id):
    """Handles summarize, annotate, and cite actions for search results."""
    current_processed_results = request.session.get('current_processed_results', {})
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
        
        # Prioritize PDF, then main_pub_url, then original url for scraping
        urls_to_try = []
        if result_data.get('pdf_url'):
            urls_to_try.append({"url": result_data['pdf_url'], "type": "pdf"})
        if result_data.get('main_pub_url') and result_data['main_pub_url'] != result_data.get('pdf_url'):
            urls_to_try.append({"url": result_data['main_pub_url'], "type": "html"})
        if url and url != result_data.get('pdf_url') and url != result_data.get('main_pub_url'):
            urls_to_try.append({"url": url, "type": "html"})
        # If the original URL was the only one and it was a PDF, ensure it's tried as HTML too
        if url and ".pdf" in url.lower() and not any(d['url'] == url and d['type'] == 'html' for d in urls_to_try):
            urls_to_try.append({"url": url, "type": "html"})

        for attempt in urls_to_try:
            temp_scraped_content, temp_scrape_error = services.scrape_article_content(attempt["url"])
            if temp_scraped_content and len(temp_scraped_content) > 200: # Consider it successful if substantial content
                scraped_content = temp_scraped_content
                text_for_summary = scraped_content
                break # Stop trying if we got good content
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

@login_required
def save_item_view(request, url_id):
    if request.method == 'POST':
        current_processed_results = request.session.get('current_processed_results', {})
        result_data = current_processed_results.get(url_id)

        if not result_data:
            messages.error(request, "Item to save not found or session expired.")
            return redirect('research_assistant:chat')

        selected_save_folder_id = request.POST.get('save_to_folder_id', 'root')
        
        folder_obj = None
        if selected_save_folder_id != "root":
            # Ensure the folder belongs to the current user
            folder_obj = get_object_or_404(Folder, user=request.user, id=selected_save_folder_id)

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
                    current_summary = "" # For journals, summary is not expected
                else:
                    messages.info(request, "Using snippet for summary as full content could not be scraped for saving.")
            
            if not is_journal_entry and text_to_summarize_for_save: # Only generate if not journal and content exists
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
        elif is_journal_entry:
            current_annotation = "" # Ensure annotation is empty for journals

        try:
            LibraryItem.objects.create(
                user=request.user,
                folder=folder_obj,
                title=title,
                url=url,
                query=query,
                source_type=source_type,
                summary=current_summary or "",
                annotation=current_annotation or "",
                content_snippet=content_snippet,
                authors=authors,
                year=year,
                pdf_url=pdf_url,
                main_pub_url=main_pub_url,
                doi=doi,
                journal_name=journal_name,
                volume=volume,
                pages=pages,
                publisher=publisher,
                issn=issn
            )
            messages.success(request, f"Item '{title[:30]}...' saved.")
            # Remove from current_processed_results after saving to avoid re-saving
            if url_id in current_processed_results:
                del current_processed_results[url_id]
                request.session['current_processed_results'] = current_processed_results
                request.session.modified = True
        except Exception as e:
            messages.error(request, f"Error saving item: {e}. It might already be saved or there was a database issue.")
        return redirect('research_assistant:chat')
    return redirect('research_assistant:chat')

@login_required
def delete_library_item_view(request, item_id):
    if request.method == 'POST':
        try:
            # Ensure the item belongs to the current user
            item = get_object_or_404(LibraryItem, user=request.user, id=item_id)
            item_title = item.title
            item.delete()
            messages.success(request, f"Item '{item_title[:30]}...' deleted.")
        except Exception as e:
            messages.error(request, f"Error deleting item: {e}")
        return redirect('research_assistant:library')
    return redirect('research_assistant:library')

@login_required
def start_new_research_session_view(request):
    # Clear session data relevant to the current research *display*
    # This does NOT clear persistent chat history or library items from the DB.
    if 'messages_display' in request.session:
        del request.session['messages_display']
    if 'current_processed_results' in request.session:
        del request.session['current_processed_results']
    if 'show_citations_search' in request.session:
        del request.session['show_citations_search']
    if 'just_submitted_initial_query' in request.session:
        del request.session['just_submitted_initial_query']
    if 'last_search_query' in request.session:
        del request.session['last_search_query']
    if 'last_exa_report' in request.session:
        del request.session['last_exa_report']
    
    request.session.modified = True
    messages.info(request, "Started a new research session. Chat display cleared.")
    return redirect('research_assistant:home')

@login_required
def clear_chat_display_view(request):
    # Clear only the session display, not the persistent history in DB
    if 'messages_display' in request.session:
        del request.session['messages_display']
        request.session.modified = True
    if 'current_processed_results' in request.session:
        del request.session['current_processed_results']
        request.session.modified = True
    if 'show_citations_search' in request.session:
        del request.session['show_citations_search']
        request.session.modified = True
    if 'last_search_query' in request.session:
        del request.session['last_search_query']
    if 'last_exa_report' in request.session:
        del request.session['last_exa_report']
    messages.info(request, "Chat display cleared.")
    return redirect('research_assistant:chat')

