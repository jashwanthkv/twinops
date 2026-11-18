"""
aie/persistence/mongodb/repositories.py — MongoDB Repositories for AIE Knowledge Entities.

Provides typed persistence and retrieval for:
- Applications & Active Version Registry
- Analysis Runs & Execution History
- Technology Profiles
- Discovered Components & Implementation Knowledge
- Relationships & Call Graphs
- Architecture Graph (Nodes & Edges)
- Contract B Application Descriptors
- Contract C Capability Catalogs
- Sanitized File Inventories
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aie.persistence.mongodb.client import MongoDBClient, mongo_client

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class BaseMongoRepository:
    """Base repository handling dynamic per-project database routing."""

    def __init__(self, client: MongoDBClient | None = None, db_name: str | None = None) -> None:
        self.client = client or mongo_client
        self.db_name = db_name

    def _col(
        self,
        collection_name: str,
        db_name: str | None = None,
        app_id: str | None = None,
    ) -> Any | None:
        target_db = db_name or self.db_name or app_id
        return self.client.get_collection(collection_name, db_name=target_db)


class ApplicationRepository(BaseMongoRepository):
    """Manages the application registry and active version pointer."""

    async def get(self, app_id: str, db_name: str | None = None) -> dict[str, Any] | None:
        col = self._col("applications", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        doc = await col.find_one({"application_id": app_id}, {"_id": 0})
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("applications")
            if fallback is not None:
                doc = await fallback.find_one({"application_id": app_id}, {"_id": 0})
        return doc

    async def get_next_version(self, app_id: str, db_name: str | None = None) -> int:
        col = self._col("applications", db_name=db_name, app_id=app_id)
        if col is None:
            return 1
        doc = await col.find_one({"application_id": app_id})
        if not doc and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("applications")
            if fallback is not None:
                doc = await fallback.find_one({"application_id": app_id})
        if doc and "active_version" in doc:
            return int(doc["active_version"]) + 1
        return 1

    async def upsert_application(
        self,
        app_id: str,
        name: str,
        repo_url: str,
        default_branch: str,
        active_run_id: str,
        version: int,
        db_name: str | None = None,
    ) -> dict[str, Any] | None:
        col = self._col("applications", db_name=db_name, app_id=app_id)
        if col is None:
            return None

        now = _now()
        update_doc = {
            "$set": {
                "application_id": app_id,
                "name": name,
                "repo_url": repo_url,
                "default_branch": default_branch,
                "active_run_id": active_run_id,
                "active_version": version,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
            "$inc": {
                "total_analyses": 1,
            },
        }
        await col.update_one({"application_id": app_id}, update_doc, upsert=True)
        return await self.get(app_id, db_name=db_name)

    async def set_active_version(
        self,
        app_id: str,
        run_id: str,
        version: int,
        db_name: str | None = None,
    ) -> bool:
        """Promote a specific analysis run as the active version for this application."""
        col = self._col("applications", db_name=db_name, app_id=app_id)
        runs_col = self._col("analysis_runs", db_name=db_name, app_id=app_id)
        comps_col = self._col("components", db_name=db_name, app_id=app_id)
        if col is None or runs_col is None:
            return False

        # Mark all previous runs as inactive
        await runs_col.update_many(
            {"application_id": app_id},
            {"$set": {"is_active": False}},
        )
        # Mark target run as active
        await runs_col.update_one(
            {"application_id": app_id, "run_id": run_id},
            {"$set": {"is_active": True}},
        )

        # Update active components
        if comps_col is not None:
            await comps_col.update_many(
                {"application_id": app_id},
                {"$set": {"is_active": False}},
            )
            await comps_col.update_many(
                {"application_id": app_id, "run_id": run_id},
                {"$set": {"is_active": True}},
            )

        # Update application document
        await col.update_one(
            {"application_id": app_id},
            {
                "$set": {
                    "active_run_id": run_id,
                    "active_version": version,
                    "updated_at": _now(),
                }
            },
        )
        logger.info("Promoted run '%s' (v%d) as active for application '%s'", run_id, version, app_id)
        return True


class AnalysisRunRepository(BaseMongoRepository):
    """Manages immutable analysis run history."""

    async def save(self, run_data: dict[str, Any], db_name: str | None = None) -> str | None:
        app_id = run_data.get("application_id")
        col = self._col("analysis_runs", db_name=db_name, app_id=app_id)
        if col is None:
            return None

        run_id = run_data.get("run_id")
        run_data["_id"] = run_id
        await col.replace_one({"run_id": run_id}, run_data, upsert=True)
        return run_id

    async def get(self, run_id: str, app_id: str | None = None, db_name: str | None = None) -> dict[str, Any] | None:
        col = self._col("analysis_runs", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        doc = await col.find_one({"run_id": run_id}, {"_id": 0})
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("analysis_runs")
            if fallback is not None:
                doc = await fallback.find_one({"run_id": run_id}, {"_id": 0})
        return doc

    async def get_latest_successful(self, app_id: str, db_name: str | None = None) -> dict[str, Any] | None:
        col = self._col("analysis_runs", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        doc = await col.find_one(
            {"application_id": app_id, "status": "complete"},
            sort=[("version", -1), ("completed_at", -1)],
            projection={"_id": 0},
        )
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("analysis_runs")
            if fallback is not None:
                doc = await fallback.find_one(
                    {"application_id": app_id, "status": "complete"},
                    sort=[("version", -1), ("completed_at", -1)],
                    projection={"_id": 0},
                )
        return doc

    async def list_runs(self, app_id: str | None = None, db_name: str | None = None) -> list[dict[str, Any]]:
        col = self._col("analysis_runs", db_name=db_name, app_id=app_id)
        if col is None:
            return []
        query = {"application_id": app_id} if app_id else {}
        cursor = col.find(query, {"_id": 0}).sort("created_at", -1)
        return await cursor.to_list(length=100)


class TechnologyProfileRepository(BaseMongoRepository):
    """Manages discovered technology profiles."""

    async def save(
        self,
        app_id: str,
        run_id: str,
        version: int,
        profile: dict[str, Any],
        db_name: str | None = None,
    ) -> None:
        col = self._col("technology_profiles", db_name=db_name, app_id=app_id)
        if col is None:
            return
        doc = {
            "_id": f"{app_id}:{run_id}",
            "application_id": app_id,
            "run_id": run_id,
            "version": version,
            **profile,
            "saved_at": _now(),
        }
        await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get(self, app_id: str, run_id: str | None = None, db_name: str | None = None) -> dict[str, Any] | None:
        col = self._col("technology_profiles", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        query: dict[str, Any] = {"application_id": app_id}
        if run_id:
            query["run_id"] = run_id
        doc = await col.find_one(query, {"_id": 0}, sort=[("version", -1)])
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("technology_profiles")
            if fallback is not None:
                doc = await fallback.find_one(query, {"_id": 0}, sort=[("version", -1)])
        return doc


class ComponentRepository(BaseMongoRepository):
    """Manages discovered components with full technical knowledge."""

    async def save_batch(
        self,
        app_id: str,
        run_id: str,
        version: int,
        components: list[dict[str, Any]],
        is_active: bool = True,
        db_name: str | None = None,
    ) -> None:
        col = self._col("components", db_name=db_name, app_id=app_id)
        if col is None or not components:
            return

        docs = []
        for comp in components:
            comp_id = comp.get("component_id") or comp.get("id") or comp.get("name")
            doc = {
                "_id": f"{app_id}:{run_id}:{comp_id}",
                "application_id": app_id,
                "run_id": run_id,
                "version": version,
                "is_active": is_active,
                **comp,
                "saved_at": _now(),
            }
            docs.append(doc)

        for doc in docs:
            await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_by_id(
        self,
        app_id: str,
        comp_id: str,
        run_id: str | None = None,
        db_name: str | None = None,
    ) -> dict[str, Any] | None:
        col = self._col("components", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        query: dict[str, Any] = {
            "application_id": app_id,
            "$or": [{"component_id": comp_id}, {"name": comp_id}],
        }
        if run_id:
            query["run_id"] = run_id
        else:
            query["is_active"] = True
        doc = await col.find_one(query, {"_id": 0})
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("components")
            if fallback is not None:
                doc = await fallback.find_one(query, {"_id": 0})
        return doc

    async def get_all(
        self,
        app_id: str,
        run_id: str | None = None,
        db_name: str | None = None,
    ) -> list[dict[str, Any]]:
        col = self._col("components", db_name=db_name, app_id=app_id)
        if col is None:
            return []
        query: dict[str, Any] = {"application_id": app_id}
        if run_id:
            query["run_id"] = run_id
        else:
            query["is_active"] = True
        cursor = col.find(query, {"_id": 0})
        results = await cursor.to_list(length=500)
        if not results and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("components")
            if fallback is not None:
                results = await fallback.find(query, {"_id": 0}).to_list(length=500)
        return results


class RelationshipRepository(BaseMongoRepository):
    """Manages discovered relationships and call graph connections."""

    async def save_batch(
        self,
        app_id: str,
        run_id: str,
        version: int,
        relationships: list[dict[str, Any]],
        db_name: str | None = None,
    ) -> None:
        col = self._col("relationships", db_name=db_name, app_id=app_id)
        if col is None or not relationships:
            return

        for idx, rel in enumerate(relationships):
            rel_id = rel.get("relationship_id") or f"rel-{idx:03d}"
            doc = {
                "_id": f"{app_id}:{run_id}:{rel_id}",
                "application_id": app_id,
                "run_id": run_id,
                "version": version,
                **rel,
                "saved_at": _now(),
            }
            await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_all(
        self,
        app_id: str,
        run_id: str | None = None,
        db_name: str | None = None,
    ) -> list[dict[str, Any]]:
        col = self._col("relationships", db_name=db_name, app_id=app_id)
        if col is None:
            return []
        query: dict[str, Any] = {"application_id": app_id}
        if run_id:
            query["run_id"] = run_id
        cursor = col.find(query, {"_id": 0})
        results = await cursor.to_list(length=1000)
        if not results and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("relationships")
            if fallback is not None:
                results = await fallback.find(query, {"_id": 0}).to_list(length=1000)
        return results


class GraphRepository(BaseMongoRepository):
    """Manages visual architecture graphs."""

    async def save_graph(
        self,
        app_id: str,
        run_id: str,
        version: int,
        graph: dict[str, Any],
        db_name: str | None = None,
    ) -> None:
        col = self._col("architecture_graphs", db_name=db_name, app_id=app_id)
        if col is None:
            return
        doc = {
            "_id": f"{app_id}:{run_id}",
            "application_id": app_id,
            "run_id": run_id,
            "version": version,
            **graph,
            "saved_at": _now(),
        }
        await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_graph(
        self,
        app_id: str,
        run_id: str | None = None,
        db_name: str | None = None,
    ) -> dict[str, Any] | None:
        col = self._col("architecture_graphs", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        query: dict[str, Any] = {"application_id": app_id}
        if run_id:
            query["run_id"] = run_id
        doc = await col.find_one(query, {"_id": 0}, sort=[("version", -1)])
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("architecture_graphs")
            if fallback is not None:
                doc = await fallback.find_one(query, {"_id": 0}, sort=[("version", -1)])
        return doc


class ContractRepository(BaseMongoRepository):
    """Manages Contract B (Application Descriptor) and Contract C (Capability Catalog)."""

    async def save_contract_b(
        self,
        app_id: str,
        run_id: str,
        version: int,
        descriptor: dict[str, Any],
        db_name: str | None = None,
    ) -> None:
        col = self._col("contract_b_descriptors", db_name=db_name, app_id=app_id)
        if col is None:
            return
        doc = {
            "_id": f"{app_id}:{run_id}",
            "application_id": app_id,
            "run_id": run_id,
            "version": version,
            "descriptor": descriptor,
            "saved_at": _now(),
        }
        await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_contract_b(
        self,
        app_id: str,
        run_id: str | None = None,
        db_name: str | None = None,
    ) -> dict[str, Any] | None:
        col = self._col("contract_b_descriptors", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        query: dict[str, Any] = {"application_id": app_id}
        if run_id:
            query["run_id"] = run_id
        doc = await col.find_one(query, {"_id": 0}, sort=[("version", -1)])
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("contract_b_descriptors")
            if fallback is not None:
                doc = await fallback.find_one(query, {"_id": 0}, sort=[("version", -1)])
        return doc.get("descriptor") if doc else None

    async def save_contract_c(
        self,
        app_id: str,
        run_id: str,
        version: int,
        capabilities: dict[str, Any],
        db_name: str | None = None,
    ) -> None:
        col = self._col("contract_c_catalogs", db_name=db_name, app_id=app_id)
        if col is None:
            return
        doc = {
            "_id": f"{app_id}:{run_id}",
            "application_id": app_id,
            "run_id": run_id,
            "version": version,
            "capabilities": capabilities,
            "saved_at": _now(),
        }
        await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_contract_c(
        self,
        app_id: str,
        run_id: str | None = None,
        db_name: str | None = None,
    ) -> dict[str, Any] | None:
        col = self._col("contract_c_catalogs", db_name=db_name, app_id=app_id)
        if col is None:
            return None
        query: dict[str, Any] = {"application_id": app_id}
        if run_id:
            query["run_id"] = run_id
        doc = await col.find_one(query, {"_id": 0}, sort=[("version", -1)])
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("contract_c_catalogs")
            if fallback is not None:
                doc = await fallback.find_one(query, {"_id": 0}, sort=[("version", -1)])
        return doc.get("capabilities") if doc else None


class FileInventoryRepository(BaseMongoRepository):
    """Manages repository file inventories."""

    async def save_inventory(
        self,
        app_id: str,
        run_id: str,
        version: int,
        files: list[dict[str, Any]],
        db_name: str | None = None,
    ) -> None:
        col = self._col("file_inventories", db_name=db_name, app_id=app_id)
        if col is None:
            return
        doc = {
            "_id": f"{app_id}:{run_id}",
            "application_id": app_id,
            "run_id": run_id,
            "version": version,
            "files": files,
            "file_count": len(files),
            "saved_at": _now(),
        }
        await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_inventory(
        self,
        app_id: str,
        run_id: str | None = None,
        db_name: str | None = None,
    ) -> list[dict[str, Any]]:
        col = self._col("file_inventories", db_name=db_name, app_id=app_id)
        if col is None:
            return []
        query: dict[str, Any] = {"application_id": app_id}
        if run_id:
            query["run_id"] = run_id
        doc = await col.find_one(query, {"_id": 0}, sort=[("version", -1)])
        if doc is None and (db_name or app_id) != self.client.db_name:
            fallback = self.client.get_collection("file_inventories")
            if fallback is not None:
                doc = await fallback.find_one(query, {"_id": 0}, sort=[("version", -1)])
        return doc.get("files", []) if doc else []
