"""
Profile module - handles user profile filling, editing, photos, and preview.

Exports:
- editing_router: Profile editing handlers (name, bio, age, interests, review, save)
- photo_router: Photo upload and management handlers
- Commands and utilities for profile management
"""

from .editing import router as editing_router
from .photo import router as photo_router

__all__ = [
    "editing_router",
    "photo_router",
]
