"""
client/components/db_service.py — Database Service with Connection Pooling & MongoDB.
Supports DB pool exhaustion / leak fault and slow query injection.
"""
from __future__ import annotations
from contextlib import contextmanager
import logging
import threading
import time
from typing import Any
from pymongo import MongoClient
from client.config import settings
from client.chaos_state import chaos

logger = logging.getLogger("client.db")

class DatabaseService:
    def __init__(self, max_pool_size: int = 10) -> None:
        self.max_pool_size = max_pool_size
        self._lock = threading.Lock()
        self._active_connections: int = 0
        self._pending_queries: int = 0
        self._mongo_client: MongoClient | None = None
        self._db = None
        self._local_fallback_data: list[dict] = [
            {"id": "prod-1", "name": "Ultra Pro Laptop 16", "price": 1299.99, "stock": 45},
            {"id": "prod-2", "name": "Wireless Noise Cancelling Headphones", "price": 199.99, "stock": 120},
            {"id": "prod-3", "name": "Smart Espresso Maker", "price": 249.50, "stock": 30},
            {"id": "prod-4", "name": "Mechanical RGB Gaming Keyboard", "price": 89.99, "stock": 80},
        ]
        self._local_orders: list[dict] = []
        # Connect asynchronously in background to ensure instant app startup
        threading.Thread(target=self._init_mongo, daemon=True).start()

    def _init_mongo(self) -> None:
        client = None
        try:
            client = MongoClient(
                settings.mongodb_uri,
                maxPoolSize=self.max_pool_size,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
            )
            client.admin.command("ping")
            self._mongo_client = client
            self._db = client[settings.mongodb_db_name]
            logger.info("Connected to MongoDB database '%s'", settings.mongodb_db_name)
            # Seed initial products
            products_col = self._db["products"]
            if products_col.count_documents({}) == 0:
                products_col.insert_many(self._local_fallback_data)
                logger.info("Seeded initial product catalog in MongoDB")
        except Exception as exc:
            logger.warning("MongoDB Atlas connection failed (%s), running with robust local in-memory DB fallback", exc)
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            self._mongo_client = None
            self._db = None

    def get_products(self) -> list[dict]:
        with self._acquire_connection():
            if chaos.db_extra_delay_ms > 0:
                time.sleep(chaos.db_extra_delay_ms / 1000.0)
            if self._db is not None:
                try:
                    cursor = self._db["products"].find({}, {"_id": 0})
                    return list(cursor)
                except Exception:
                    pass
            return list(self._local_fallback_data)

    def create_order(self, order_data: dict) -> dict:
        with self._acquire_connection():
            if chaos.db_extra_delay_ms > 0:
                time.sleep(chaos.db_extra_delay_ms / 1000.0)
            order_data["created_at"] = time.time()
            if self._db is not None:
                try:
                    res = self._db["orders"].insert_one(order_data.copy())
                    order_data["order_id"] = str(res.inserted_id)
                    return order_data
                except Exception:
                    pass
            order_data["order_id"] = f"ord-{len(self._local_orders) + 1}"
            self._local_orders.append(order_data)
            return order_data

    @contextmanager
    def _acquire_connection(self):
        with self._lock:
            if self._active_connections >= self.max_pool_size:
                self._pending_queries += 1
            else:
                self._active_connections += 1

        try:
            yield
        finally:
            with self._lock:
                if not chaos.db_leak_enabled:
                    self._active_connections = max(0, self._active_connections - 1)
                    self._pending_queries = max(0, self._pending_queries - 1)
                else:
                    # In DB leak mode, connections do not return to pool!
                    self._active_connections = self.max_pool_size
                    self._pending_queries = min(25, self._pending_queries + 1)

    def get_pool_stats(self) -> dict[str, float]:
        with self._lock:
            if chaos.db_leak_enabled:
                active = float(self.max_pool_size)
                idle = 0.0
                pending = float(min(25, max(10, self._pending_queries)))
                saturation = 1.0
            else:
                active = float(self._active_connections)
                idle = float(max(0, self.max_pool_size - self._active_connections))
                pending = float(self._pending_queries)
                saturation = round(active / self.max_pool_size, 3)

            return {
                "db_pool_active": active,
                "db_pool_idle": idle,
                "db_pool_pending": pending,
                "db_pool_saturation_ratio": saturation,
            }

db_service = DatabaseService()
