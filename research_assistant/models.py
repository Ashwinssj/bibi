# research_assistant/models.py
from django.db import models
from django.contrib.auth.models import User # Django's built-in User model
import uuid # For unique IDs

class Folder(models.Model):
    """
    Represents a folder created by a user to organize library items.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='folders')
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Ensure a user cannot have two folders with the exact same name
        unique_together = ('user', 'name')
        ordering = ['name'] # Order folders alphabetically by name

    def __str__(self):
        return f"{self.name} (User: {self.user.username})"

class LibraryItem(models.Model):
    """
    Represents a research item saved by a user.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='library_items')
    # If a folder is deleted, items in it will have folder set to NULL (root)
    folder = models.ForeignKey(Folder, on_delete=models.SET_NULL, null=True, blank=True, related_name='items')
    
    title = models.CharField(max_length=512)
    url = models.URLField(max_length=2048)
    query = models.TextField(blank=True) # The original query that led to this item
    source_type = models.CharField(max_length=100, default="Website")
    added_timestamp = models.DateTimeField(auto_now_add=True)

    # Generated content
    summary = models.TextField(blank=True)
    annotation = models.TextField(blank=True)
    content_snippet = models.TextField(blank=True) # The initial snippet from search

    # Bibliographic details (from search results)
    authors = models.CharField(max_length=512, blank=True)
    year = models.CharField(max_length=10, blank=True) # Can be '2023', 'N.D.', 'forthcoming'
    pdf_url = models.URLField(max_length=2048, blank=True, null=True)
    main_pub_url = models.URLField(max_length=2048, blank=True, null=True)
    doi = models.CharField(max_length=255, blank=True)
    journal_name = models.CharField(max_length=255, blank=True)
    volume = models.CharField(max_length=50, blank=True)
    pages = models.CharField(max_length=50, blank=True)
    publisher = models.CharField(max_length=255, blank=True) # New field for journals
    issn = models.CharField(max_length=255, blank=True) # New field for journals

    class Meta:
        # A user cannot save the exact same URL twice (assuming URL is unique enough for an item)
        # This will prevent duplicate entries for the same user.
        unique_together = ('user', 'url')
        ordering = ['-added_timestamp'] # Order by most recently added

    def __str__(self):
        folder_name = self.folder.name if self.folder else 'Root'
        return f"{self.title[:50]}... (User: {self.user.username}, Folder: {folder_name})"

class ChatMessage(models.Model):
    """
    Stores chat messages for a user persistently.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_messages')
    role = models.CharField(max_length=20) # 'user' or 'assistant'
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['timestamp'] # Order by time to maintain chat flow

    def __str__(self):
        return f"{self.user.username} ({self.role}): {self.content[:50]}..."

