from django.urls import path
from . import views

app_name = 'research_assistant'

urlpatterns = [
    path('', views.home_view, name='home'),
    path('chat/', views.chat_view, name='chat'),
    path('library/', views.library_view, name='library'),
    path('create_folder/', views.create_folder_view, name='create_folder'),
    path('delete_folder/<str:folder_id>/', views.delete_folder_view, name='delete_folder'),
    path('process_result/<path:url_id>/', views.process_result_view, name='process_result'), # Using path for URL_id
    path('save_item/<path:url_id>/', views.save_item_view, name='save_item'), # Using path for URL_id
    path('delete_library_item/<str:item_key>/', views.delete_library_item_view, name='delete_library_item'),
    path('start_new_research/', views.start_new_research_session_view, name='start_new_research'),
    path('clear_chat_display/', views.clear_chat_display_view, name='clear_chat_display'),
]
