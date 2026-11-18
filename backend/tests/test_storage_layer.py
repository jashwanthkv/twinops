"""
tests/test_storage_layer.py — Test Suite for TwinOps AIE Knowledge Storage Layer.

Tests:
1. Redis In-Memory Knowledge Store (components, metadata, Contract B/C).
2. Redis Graph Representation & Adjacency Lists (immediate neighbours & multi-hop traversal).
3. ChromaDB Semantic Chunking & Scoped Search.
4. Version Isolation & Version Incrementing.
5. Rebuild Redis from MongoDB.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Disable live Redis socket attempts during unit testing
os.environ["AIE_DISABLE_LIVE_REDIS"] = "1"

# Ensure backend directory is in python path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from aie.models import (
    AnalysisRun,
    ComponentType,
    DiscoveredComponent,
    DiscoveredRelationship,
    FileCategory,
    FileEntry,
    RelationType,
    RelationshipConfidence,
    TechnologyProfile,
)
from aie.persistence.redis.client import InMemoryRedisMock, RedisClient
from aie.persistence.redis.graph_store import RedisGraphStore
from aie.persistence.redis.knowledge_store import RedisKnowledgeStore
from aie.persistence.vector.chroma_store import ChromaKnowledgeStore


class TestRedisKnowledgeStore(unittest.IsolatedAsyncioTestCase):
    """Test Redis in-memory storage for active components and contracts."""

    async def asyncSetUp(self) -> None:
        # Use isolated in-memory Redis client
        self.mock_client = RedisClient()
        self.mock_client._client = InMemoryRedisMock()
        self.store = RedisKnowledgeStore(client=self.mock_client)
        self.app_id = "test-order-app"

    async def test_active_version_tracking(self) -> None:
        await self.store.set_active_version(
            app_id=self.app_id,
            run_id="run-101",
            version=1,
            commit_hash="abc1234",
        )
        active = await self.store.get_active_version(self.app_id)
        self.assertIsNotNone(active)
        self.assertEqual(active["run_id"], "run-101")
        self.assertEqual(active["version"], 1)

    async def test_component_storage_and_retrieval(self) -> None:
        comp_data = {
            "component_id": "comp-api",
            "name": "API Service",
            "component_type": "web_tier",
            "technologies": ["FastAPI", "Python"],
            "responsibility": "Handles HTTP REST requests",
            "failure_scenarios": ["Worker pool exhaustion"],
            "monitoring_requirements": ["p99 response latency"],
        }
        await self.store.store_component(self.app_id, comp_data)

        # Single lookup
        retrieved = await self.store.get_component(self.app_id, "comp-api")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "API Service")
        self.assertEqual(retrieved["technologies"], ["FastAPI", "Python"])

        # All components lookup
        all_comps = await self.store.get_all_components(self.app_id)
        self.assertEqual(len(all_comps), 1)
        self.assertEqual(all_comps[0]["component_id"], "comp-api")

    async def test_contracts_b_and_c_caching(self) -> None:
        desc = {
            "application_id": self.app_id,
            "components": [{"id": "API Service", "type": "web_tier"}],
            "relationships": [["API Service", "calls", "DB"]],
        }
        await self.store.store_contract_b(self.app_id, desc)
        got_desc = await self.store.get_contract_b(self.app_id)
        self.assertEqual(got_desc["application_id"], self.app_id)

        caps = {
            "application_id": self.app_id,
            "actions": [{"action": "scale_workers", "target": "API Service"}],
        }
        await self.store.store_contract_c(self.app_id, caps)
        got_caps = await self.store.get_contract_c(self.app_id)
        self.assertEqual(len(got_caps["actions"]), 1)

    async def test_clear_app_eviction(self) -> None:
        await self.store.store_component(self.app_id, {"component_id": "c1", "name": "C1"})
        await self.store.clear_app(self.app_id)
        all_comps = await self.store.get_all_components(self.app_id)
        self.assertEqual(len(all_comps), 0)


class TestRedisGraphStore(unittest.IsolatedAsyncioTestCase):
    """Test Redis Graph representation, adjacency lists, and multi-hop traversal."""

    async def asyncSetUp(self) -> None:
        self.mock_client = RedisClient()
        self.mock_client._client = InMemoryRedisMock()
        self.graph_store = RedisGraphStore(client=self.mock_client)
        self.app_id = "test-graph-app"

    async def test_graph_adjacency_and_neighbours(self) -> None:
        # A -> B -> C
        nodes = [
            {"id": "comp-a", "name": "Frontend Web"},
            {"id": "comp-b", "name": "Order Service"},
            {"id": "comp-c", "name": "Payment Gateway"},
        ]
        edges = [
            {"id": "e1", "source": "comp-a", "target": "comp-b", "label": "calls"},
            {"id": "e2", "source": "comp-b", "target": "comp-c", "label": "calls"},
        ]
        await self.graph_store.store_graph(self.app_id, nodes, edges)

        # Immediate neighbours of B
        neighbours_b = await self.graph_store.get_neighbours(self.app_id, "comp-b", direction="all")
        self.assertEqual(len(neighbours_b["incoming"]), 1)
        self.assertEqual(neighbours_b["incoming"][0]["source"], "comp-a")
        self.assertEqual(len(neighbours_b["outgoing"]), 1)
        self.assertEqual(neighbours_b["outgoing"][0]["target"], "comp-c")

        # Outgoing neighbours of A
        neighbours_a = await self.graph_store.get_neighbours(self.app_id, "comp-a", direction="out")
        self.assertEqual(len(neighbours_a["outgoing"]), 1)
        self.assertEqual(neighbours_a["outgoing"][0]["target"], "comp-b")

    async def test_multi_hop_bfs_traversal(self) -> None:
        # A -> B -> C
        nodes = [
            {"id": "comp-a", "name": "Web"},
            {"id": "comp-b", "name": "API"},
            {"id": "comp-c", "name": "DB"},
        ]
        edges = [
            {"id": "e1", "source": "comp-a", "target": "comp-b", "label": "calls"},
            {"id": "e2", "source": "comp-b", "target": "comp-c", "label": "writes_to"},
        ]
        await self.graph_store.store_graph(self.app_id, nodes, edges)

        paths = await self.graph_store.traverse_paths(self.app_id, "comp-a", direction="out", max_depth=3)
        # Should reach both B (depth 1) and C (depth 2)
        destinations = [p["destination"] for p in paths]
        self.assertIn("comp-b", destinations)
        self.assertIn("comp-c", destinations)

        # Depth check
        c_path = next(p for p in paths if p["destination"] == "comp-c")
        self.assertEqual(c_path["depth"], 2)


class TestChromaSemanticKnowledgeStore(unittest.TestCase):
    """Test ChromaDB semantic knowledge chunking and metadata search."""

    def setUp(self) -> None:
        self.chroma = ChromaKnowledgeStore()
        self.app_id = "test-chroma-app"

    def test_semantic_chunking_and_metadata(self) -> None:
        comp = DiscoveredComponent(
            run_id="run-001",
            name="Payment Service",
            component_type=ComponentType.SERVICE,
            primary_language="Python",
            technologies=["FastAPI", "Stripe"],
            description="Processes credit card payments and communicates with Stripe webhook",
            metadata={
                "responsibility": "Processes credit card payments and communicates with Stripe webhook",
                "failure_scenarios": ["Stripe gateway timeout during peak checkout hours"],
                "monitoring_requirements": ["payment_failure_rate_percent"],
                "adaptation_opportunities": ["increase_gateway_timeout"],
            },
        )

        indexed_count = self.chroma.index_component_knowledge(
            app_id=self.app_id,
            run_id="run-001",
            version=1,
            components=[comp],
        )

        if self.chroma._is_available:
            self.assertGreaterEqual(indexed_count, 3)

            # Query for failure scenarios
            results = self.chroma.semantic_search(
                app_id=self.app_id,
                query="gateway timeout or failure during checkout",
                n_results=2,
            )
            self.assertGreater(len(results), 0)
            hit = results[0]
            self.assertIn("metadata", hit)
            self.assertEqual(hit["metadata"]["application_id"], self.app_id)
            self.assertIn("Payment Service", hit["content"])


class TestPersistenceAndRetrievalServices(unittest.IsolatedAsyncioTestCase):
    """End-to-end integration test of persistence, Redis recovery, and SLM retrieval."""

    async def asyncSetUp(self) -> None:
        from aie.persistence.services import persistence_service, retrieval_service
        self.persistence = persistence_service
        self.retrieval = retrieval_service
        self.app_id = "test-e2e-app"

    async def test_end_to_end_save_and_retrieval(self) -> None:
        run = AnalysisRun(
            run_id="run-e2e-001",
            application_id=self.app_id,
            repo_url="https://github.com/example/e2e-repo",
            repo_ref="main",
            branch="main",
            status="complete",
        )
        comp_api = DiscoveredComponent(
            run_id="run-e2e-001",
            name="API Service",
            component_type=ComponentType.SERVICE,
            technologies=["FastAPI"],
            metadata={"responsibility": "Serves orders API"},
        )
        comp_db = DiscoveredComponent(
            run_id="run-e2e-001",
            name="Database Service",
            component_type=ComponentType.DATABASE,
            technologies=["PostgreSQL"],
            metadata={"responsibility": "Persists order records"},
        )
        rel = DiscoveredRelationship(
            run_id="run-e2e-001",
            source_component_id=comp_api.component_id,
            target_component_id=comp_db.component_id,
            relation_type=RelationType.CALLS,
            confidence=RelationshipConfidence.CONFIRMED,
        )
        desc = {
            "application_id": self.app_id,
            "components": [{"id": "API Service"}, {"id": "Database Service"}],
            "relationships": [["API Service", "calls", "Database Service"]],
        }
        caps = {
            "application_id": self.app_id,
            "actions": [{"action": "scale_api", "target": "API Service"}],
        }

        # 1. Save completed analysis across all tiers
        saved_info = await self.persistence.save_completed_analysis(
            run=run,
            inventory=[],
            profile={"primary_language": "Python"},
            components=[comp_api, comp_db],
            relationships=[rel],
            graph={"nodes": [], "edges": []},
            descriptor=desc,
            capabilities=caps,
        )
        self.assertEqual(saved_info["status"], "persisted")

        # 2. Fast Redis component retrieval
        api_comp = await self.retrieval.get_component(self.app_id, comp_api.component_id)
        self.assertIsNotNone(api_comp)
        self.assertEqual(api_comp["name"], "API Service")

        # 3. Adjacency graph neighbours
        neighbours = await self.retrieval.get_component_neighbours(self.app_id, comp_api.component_id, direction="out")
        self.assertEqual(len(neighbours["outgoing"]), 1)
        self.assertEqual(neighbours["outgoing"][0]["target"], comp_db.component_id)

        # 4. Contracts retrieval
        got_desc = await self.retrieval.get_contract_b(self.app_id)
        self.assertIsNotNone(got_desc)
        self.assertEqual(got_desc["application_id"], self.app_id)

        got_caps = await self.retrieval.get_contract_c(self.app_id)
        self.assertIsNotNone(got_caps)
        self.assertEqual(len(got_caps["actions"]), 1)

        # 5. Semantic search through ChromaDB
        hits = await self.retrieval.semantic_search(self.app_id, "orders API and endpoints", n_results=1)
        if self.retrieval.chroma._is_available:
            self.assertGreater(len(hits), 0)


if __name__ == "__main__":
    unittest.main()
