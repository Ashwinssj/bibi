# research_assistant/admin.py
from django.contrib import admin
from .models import Folder, LibraryItem, ChatMessage

admin.site.register(Folder)
admin.site.register(LibraryItem)
admin.site.register(ChatMessage)

