"""
aie/persistence/mongodb/client.py — Asynchronous MongoDB Client & Connection Manager.

Provides connection pooling, life-cycle management, ping verification, and
collection access using motor (asyncio driver for MongoDB).
"""

from __future__ import annotations

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, PyMongoError

from aie.config import settings

logger = logging.getLogger(__name__)


class MongoDBClient:
    """Manages asynchronous MongoDB connection lifecycle and database handles."""

    def __init__(self, uri: str | None = None, db_name: str | None = None) -> None:
        self.uri = uri or settings.mongodb_uri
        self.db_name = db_name or settings.mongodb_database
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None
        self._is_connected: bool = False

    async def connect(self) -> bool:
        """Establish connection to MongoDB and verify with a ping."""
        if self._is_connected and self._client is not None:
            return True

        if not self.uri:
            logger.warning("MongoDB URI is not configured. Running in disconnected mode.")
            self._is_connected = False
            return False

        try:
            # serverSelectionTimeoutMS prevents hanging when cluster is unreachable
            self._client = AsyncIOMotorClient(
                self.uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
            )
            # Test ping
            await self._client.admin.command("ping")
            self._db = self._client[self.db_name]
            self._is_connected = True
            logger.info("Successfully connected to MongoDB database '%s'", self.db_name)

            # Ensure indexes in background
            await self._create_indexes()
            return True
        except (ConnectionFailure, PyMongoError, Exception) as exc:
            logger.warning(
                "Could not connect to MongoDB (%s). Persistence will fall back gracefully: %s",
                self.db_name,
                exc,
            )
            self._client = None
            self._db = None
            self._is_connected = False
            return False

    async def disconnect(self) -> None:
        """Close connection cleanly on application shutdown."""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
            self._is_connected = False
            logger.info("MongoDB client disconnected.")

    @property
    def is_connected(self) -> bool:
        return self._is_connected and self._db is not None

    def get_database(self, db_name: str | None = None) -> AsyncIOMotorDatabase | None:
        """Return database instance if connected, targeting db_name if provided."""
        if not self.is_connected or self._client is None:
            return None
        target = db_name or self.db_name
        return self._client[target]

    def get_collection(self, name: str, db_name: str | None = None) -> Any | None:
        """Get collection handle by name from target database."""
        db = self.get_database(db_name)
        if db is None:
            return None
        return db[name]

    async def create_indexes_for_db(self, db_name: str) -> None:
        """Ensure indexes on target project database."""
        if not self.is_connected or self._client is None:
            return
        db = self._client[db_name]
        try:
            await db.applications.create_index("application_id", unique=True)
            await db.analysis_runs.create_index([("application_id", 1), ("version", -1)])
            await db.analysis_runs.create_index("run_id", unique=True)
            await db.components.create_index([("application_id", 1), ("run_id", 1), ("component_id", 1)], unique=True)
            await db.relationships.create_index([("application_id", 1), ("run_id", 1)])
            await db.contract_b_descriptors.create_index([("application_id", 1), ("run_id", 1)])
            await db.contract_c_catalogs.create_index([("application_id", 1), ("run_id", 1)])
            logger.info("MongoDB indexes verified on database '%s'", db_name)
        except Exception as exc:
            logger.debug("Index creation note on '%s': %s", db_name, exc)

    async def _create_indexes(self) -> None:
        """Create required indexes for fast retrieval and version isolation."""
        if not self.is_connected or self._db is None:
            return

        try:
            # Applications index
            await self._db.applications.create_index("application_id", unique=True)

            # Analysis runs indexes
            await self._db.analysis_runs.create_index([("application_id", 1), ("version", -1)])
            await self._db.analysis_runs.create_index([("application_id", 1), ("is_active", 1)])
            await self._db.analysis_runs.create_index("run_id", unique=True)

            # Components indexes
            await self._db.components.create_index(
                [("application_id", 1), ("run_id", 1), ("component_id", 1)],
                unique=True,
            )
            await self._db.components.create_index([("application_id", 1), ("is_active", 1)])

            # Relationships indexes
            await self._db.relationships.create_index([("application_id", 1), ("run_id", 1)])
            await self._db.relationships.create_index(
                [("application_id", 1), ("source_component_id", 1)]
            )
            await self._db.relationships.create_index(
                [("application_id", 1), ("target_component_id", 1)]
            )

            # Contract B & C indexes
            await self._db.contract_b_descriptors.create_index(
                [("application_id", 1), ("run_id", 1)]
            )
            await self._db.contract_c_catalogs.create_index([("application_id", 1), ("run_id", 1)])

            # Graph index
            await self._db.architecture_graphs.create_index([("application_id", 1), ("run_id", 1)])

            logger.info("MongoDB indexes verified.")
        except Exception as exc:
            logger.warning("Error creating MongoDB indexes: %s", exc)


# Singleton instance
mongo_client = MongoDBClient()
