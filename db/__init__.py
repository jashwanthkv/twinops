"""
client/db/__init__.py — Database package exports.
"""
from client.db.db_service import db_service, MongoDatabaseService

__all__ = ["db_service", "MongoDatabaseService"]
