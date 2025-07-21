# research_assistant/urls.py
from django.urls import path
from . import views
from django.contrib.auth.decorators import login_required

app_name = 'research_assistant'

urlpatterns = [
    # All these views now require a user to be logged in
    path('', login_required(views.home_view), name='home'), # Home now requires login
    path('chat/', login_required(views.chat_view), name='chat'),
    path('library/', login_required(views.library_view), name='library'),
    path('create_folder/', login_required(views.create_folder_view), name='create_folder'),
    path('delete_folder/<uuid:folder_id>/', login_required(views.delete_folder_view), name='delete_folder'), # Changed to UUID
    path('process_result/<path:url_id>/', login_required(views.process_result_view), name='process_result'), # Using path for URL_id (which can contain slashes)
    path('save_item/<path:url_id>/', login_required(views.save_item_view), name='save_item'), # Using path for URL_id
    path('delete_library_item/<uuid:item_id>/', login_required(views.delete_library_item_view), name='delete_library_item'), # Changed to UUID
    path('start_new_research/', login_required(views.start_new_research_session_view), name='start_new_research'),
    path('clear_chat_display/', login_required(views.clear_chat_display_view), name='clear_chat_display'),
]

