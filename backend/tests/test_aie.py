"""
Unit & Integration Test Suite for TwinOps Application Intelligence Engine (AIE).
Tests all core modules and API endpoints.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure backend directory is in python path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import httpx

from aie.classifier.classifier import FileClassifier
from aie.classifier.filter import FileFilter
from aie.classifier.language_detector import LanguageDetector
from aie.classifier.inventory import InventoryBuilder
from aie.components.identifier import ComponentIdentifier
from aie.components.scoring import ComponentScorer
from aie.config import AIEConfig
from aie.discovery.engine import DiscoveryEngine
from aie.docs_analyzer.secret_filter import SecretFilter
from aie.docs_analyzer.config_extractor import ConfigExtractor
from aie.docs_analyzer.doc_parser import DocParser
from aie.enrichment.enricher import TechnicalEnricher
from aie.graph.descriptor_gen import DescriptorGenerator
from aie.graph.generator import ArchitectureGraphGenerator
from aie.ingestion.security import SecurityChecker, PathTraversalError
from aie.models import (
    AnalysisRequest,
    AnalysisRun,
    ComponentType,
    DiscoveredComponent,
    DiscoveredRelationship,
    EvidenceType,
    FileCategory,
    FileEntry,
    RelationType,
    RelationshipConfidence,
    SourceEvidence,
    TechnologyProfile,
)
from aie.persistence.store import AIEStore, aie_store
from aie.relationships.analyzer import RelationshipAnalyzer
from aie.static_analysis.parsers.python_parser import PythonASTParser
from aie.static_analysis.ir import CodebaseIR
from aie.validation.confidence import ConfidenceCalculator
from aie.validation.discrepancy import DiscrepancyDetector
from main import app


class TestSecurityChecker(unittest.TestCase):
    def test_path_traversal_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            safe_file = workspace / "app" / "main.py"
            # Safe path inside workspace should not raise
            try:
                SecurityChecker.check_path_traversal(safe_file, workspace)
            except PathTraversalError:
                self.fail("Safe path unexpectedly raised PathTraversalError")

            # Dangerous path escaping workspace must raise PathTraversalError
            dangerous_path = workspace / ".." / "secret.txt"
            with self.assertRaises(PathTraversalError):
                SecurityChecker.check_path_traversal(dangerous_path, workspace)

    def test_secret_file_detection(self):
        self.assertTrue(SecurityChecker.is_secret_file(".env"))
        self.assertTrue(SecurityChecker.is_secret_file(".env.production"))
        self.assertTrue(SecurityChecker.is_secret_file("id_rsa"))
        self.assertTrue(SecurityChecker.is_secret_file("server.key"))
        self.assertTrue(SecurityChecker.is_secret_file("cert.pem"))
        self.assertFalse(SecurityChecker.is_secret_file("main.py"))
        self.assertFalse(SecurityChecker.is_secret_file("README.md"))


class TestSecretFilter(unittest.TestCase):
    def setUp(self):
        self.filter = SecretFilter()

    def test_secret_key_detection(self):
        self.assertTrue(self.filter.is_secret_key("API_KEY"))
        self.assertTrue(self.filter.is_secret_key("db_password"))
        self.assertTrue(self.filter.is_secret_key("jwt_secret"))
        self.assertFalse(self.filter.is_secret_key("app_port"))
        self.assertFalse(self.filter.is_secret_key("database_host"))

    def test_redact_dict(self):
        data = {
            "DB_HOST": "localhost",
            "DB_PORT": 5432,
            "DB_PASSWORD": "supersecretpassword123",
            "nested": {
                "SECRET_TOKEN": "ghp_123456789012345678901234567890123456",
                "normal": "value",
            },
        }
        filtered = self.filter.filter_dict(data)
        self.assertEqual(filtered["DB_HOST"], "localhost")
        self.assertEqual(filtered["DB_PASSWORD"], "[REDACTED]")
        self.assertEqual(filtered["nested"]["SECRET_TOKEN"], "[REDACTED]")
        self.assertEqual(filtered["nested"]["normal"], "value")


class TestFileClassifierAndInventory(unittest.TestCase):
    def setUp(self):
        self.classifier = FileClassifier()
        self.lang_detector = LanguageDetector()
        self.file_filter = FileFilter()

    def test_language_detection(self):
        self.assertEqual(self.lang_detector.detect(Path("app/main.py")), "python")
        self.assertEqual(self.lang_detector.detect(Path("src/index.js")), "javascript")
        self.assertEqual(self.lang_detector.detect(Path("src/app.tsx")), "typescript")
        self.assertEqual(self.lang_detector.detect(Path("App.java")), "java")

    def test_file_categorization(self):
        self.assertEqual(self.classifier.classify(Path("app/main.py")), FileCategory.SOURCE_CODE)
        self.assertEqual(self.classifier.classify(Path("requirements.txt")), FileCategory.DEPENDENCY_MANIFEST)
        self.assertEqual(self.classifier.classify(Path("package.json")), FileCategory.DEPENDENCY_MANIFEST)
        self.assertEqual(self.classifier.classify(Path("README.md")), FileCategory.DOCUMENTATION)
        self.assertEqual(self.classifier.classify(Path("Dockerfile")), FileCategory.INFRASTRUCTURE)
        self.assertEqual(self.classifier.classify(Path("tests/test_api.py")), FileCategory.TEST)

    def test_filter_exclusions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            td = Path(temp_dir)
            git_file = td / ".git" / "config"
            git_file.parent.mkdir(parents=True, exist_ok=True)
            git_file.write_text("dummy", encoding="utf-8")

            valid_file = td / "src" / "main.py"
            valid_file.parent.mkdir(parents=True, exist_ok=True)
            valid_file.write_text("print('hello')", encoding="utf-8")

            self.assertFalse(self.file_filter.should_include(git_file))
            self.assertTrue(self.file_filter.should_include(valid_file))


class TestPythonASTParser(unittest.TestCase):
    def setUp(self):
        self.parser = PythonASTParser()

    def test_parse_python_source(self):
        source = '''
from fastapi import FastAPI, APIRouter
import redis

app = FastAPI()
router = APIRouter()

@app.get("/items")
async def get_items():
    r = redis.Redis(host="localhost", port=6379)
    return {"status": "ok"}

class UserService:
    def __init__(self, db):
        self.db = db

    def get_user(self, user_id):
        return self.db.find(user_id)
'''
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(source)
            f_path = Path(f.name)

        try:
            ir = self.parser.parse_file(f_path)
            self.assertFalse(ir.parse_error)

            # Check classes
            class_names = [cls.name for cls in ir.classes]
            self.assertIn("UserService", class_names)

            # Check functions
            func_names = [fn.name for fn in ir.functions]
            self.assertIn("get_items", func_names)

            # Check imports
            import_modules = [imp.module for imp in ir.imports]
            self.assertTrue(any("fastapi" in str(m) for m in import_modules))
            self.assertTrue(any("redis" in str(m) for m in import_modules))

            # Check framework patterns
            patterns = [p.type for p in ir.framework_patterns]
            self.assertTrue(any("route" in p.lower() or "redis" in p.lower() for p in patterns))
        finally:
            if f_path.exists():
                os.unlink(f_path)


class TestDiscoveryAndDocsAnalyzer(unittest.TestCase):
    def test_docs_and_config_extraction(self):
        extractor = ConfigExtractor()
        doc_parser = DocParser()

        with tempfile.TemporaryDirectory() as temp_dir:
            ws = Path(temp_dir)
            # Create a sample .env.example
            env_file = ws / ".env.example"
            env_file.write_text("DATABASE_URL=postgresql://user:pass@localhost:5432/app\nPOOL_SIZE=20\nTIMEOUT=30\n", encoding="utf-8")

            # Create a sample README.md
            readme = ws / "README.md"
            readme.write_text("# Test App\nA scalable microservice with Redis cache and PostgreSQL.\n", encoding="utf-8")

            # Extract configs
            inv = [FileEntry(path=".env.example"), FileEntry(path="README.md")]
            findings = extractor.extract(ws, inv)
            keys = [f.key for f in findings]
            self.assertIn("POOL_SIZE", keys)
            self.assertIn("TIMEOUT", keys)

            # Parse docs
            docs_knowledge = doc_parser.parse(ws, inv)
            techs_lower = [t.lower() for t in docs_knowledge.technologies_mentioned]
            self.assertTrue("redis" in techs_lower or "postgresql" in techs_lower)


class TestComponentIdentificationAndEnrichment(unittest.TestCase):
    def test_enrichment(self):
        enricher = TechnicalEnricher()
        comp = DiscoveredComponent(
            component_id="comp-redis-1",
            run_id="run-1",
            name="Redis Cache",
            logical_type=ComponentType.CACHE,
            technology="Redis",
            analysis_id="run-1",
        )
        enricher.enrich([comp], TechnologyProfile(run_id="run-1"))
        # Should have attached failure scenarios and monitoring requirements into metadata
        failures = comp.metadata.get("failure_scenarios", [])
        self.assertGreater(len(failures), 0)
        self.assertTrue(any("memory" in str(s).lower() or "eviction" in str(s).lower() for s in failures))


class TestConfidenceAndDiscrepancy(unittest.TestCase):
    def test_confidence_calculation(self):
        calculator = ConfidenceCalculator()
        comp = DiscoveredComponent(
            component_id="comp-1",
            run_id="run-1",
            name="Web Service",
            logical_type=ComponentType.WEB_TIER,
            evidence=[
                SourceEvidence(evidence_type=EvidenceType.STATIC_ANALYSIS, confidence=0.8),
                SourceEvidence(evidence_type=EvidenceType.CONFIGURATION, confidence=0.9),
            ],
            analysis_id="run-1",
        )
        score = calculator.calculate(comp)
        self.assertGreaterEqual(score, 0.5)
        self.assertLessEqual(score, 1.0)


class TestGraphAndDescriptorGenerator(unittest.TestCase):
    def test_descriptor_generation(self):
        comp1 = DiscoveredComponent(
            component_id="comp-web",
            run_id="test-analysis",
            name="Web API",
            logical_type=ComponentType.WEB_TIER,
            technology="FastAPI",
            responsibility="Handles HTTP requests",
            implementation_summary="FastAPI router",
            adaptation_opportunities=[
                {
                    "action": "scale_workers",
                    "target_config": "workers",
                    "risk": "low",
                    "reversible": True,
                    "min": 1,
                    "max": 10,
                }
            ],
            confidence_score=0.9,
            analysis_id="test-analysis",
        )
        comp2 = DiscoveredComponent(
            component_id="comp-cache",
            run_id="test-analysis",
            name="Redis Cache",
            logical_type=ComponentType.CACHE,
            technology="Redis",
            responsibility="Caches responses",
            implementation_summary="Redis client",
            adaptation_opportunities=[],
            confidence_score=0.85,
            analysis_id="test-analysis",
        )

        rel = DiscoveredRelationship(
            relationship_id="rel-1",
            run_id="test-analysis",
            source_component_id="comp-web",
            target_component_id="comp-cache",
            relation_type=RelationType.CALLS,
            confidence=RelationshipConfidence.CONFIRMED,
        )

        profile = TechnologyProfile(
            run_id="test-analysis",
            primary_language="Python",
            primary_framework="FastAPI",
        )

        # Architecture Graph Generator
        graph_gen = ArchitectureGraphGenerator()
        graph = graph_gen.generate([comp1, comp2], [rel])
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(len(graph.edges), 1)

        # Descriptor Generator (Contract B)
        desc_gen = DescriptorGenerator()
        descriptor = desc_gen.generate_descriptor("app-001", [comp1, comp2], [rel], profile)

        self.assertEqual(descriptor["application_id"], "app-001")
        self.assertEqual(len(descriptor["components"]), 2)
        self.assertEqual(len(descriptor["relationships"]), 1)

        # Capabilities Catalog (Contract C)
        caps = desc_gen.infer_capabilities([comp1, comp2])
        self.assertEqual(caps["application_id"], "test-analysis")
        self.assertGreaterEqual(len(caps["actions"]), 1)
        action_names = [a["action"] for a in caps["actions"]]
        self.assertTrue("scale_workers" in action_names or "adjust_cache_ttl" in action_names)


from aie.api.routes import (
    list_analyses,
    get_analysis,
    get_profile,
    get_components,
    get_descriptor,
)
import main


class TestAPIEndpoints(unittest.TestCase):
    def test_root_and_health(self):
        res = asyncio.run(main.root())
        self.assertEqual(res["service"], "TwinOps AIE")

        res_health = asyncio.run(main.health())
        self.assertEqual(res_health["status"], "ok")

    def test_analyses_endpoints(self):
        res = asyncio.run(list_analyses(store=aie_store))
        self.assertIsInstance(res, list)

    def test_stored_records_endpoints(self):
        run = AnalysisRun(
            run_id="run-test-123",
            application_id="app-test-123",
            repo_url="https://github.com/example/test-repo",
            status="completed",
        )
        aie_store.store_analysis(run)

        comp = DiscoveredComponent(
            component_id="comp-1",
            run_id="run-test-123",
            name="API Service",
            logical_type=ComponentType.WEB_TIER,
            technology="FastAPI",
            responsibility="REST API",
            implementation_summary="Routes and handlers",
            analysis_id="run-test-123",
        )
        aie_store.store_components("app-test-123", [comp])

        profile = TechnologyProfile(run_id="run-test-123", primary_language="Python", primary_framework="FastAPI")
        aie_store.store_profile("app-test-123", profile)

        desc = {
            "application_id": "app-test-123",
            "components": [{"id": "comp-1", "type": "web_tier", "technology": "FastAPI"}],
            "relationships": [],
        }
        aie_store.store_descriptor("app-test-123", desc)

        # Query GET /analyses/{id}
        res_run = asyncio.run(get_analysis("run-test-123", store=aie_store))
        self.assertEqual(res_run["run_id"], "run-test-123")

        # Query GET /apps/{app_id}/components
        res_comp = asyncio.run(get_components("app-test-123", store=aie_store))
        self.assertEqual(len(res_comp), 1)
        self.assertEqual(res_comp[0]["name"], "API Service")

        # Query GET /apps/{app_id}/profile
        res_prof = asyncio.run(get_profile("app-test-123", store=aie_store))
        self.assertEqual(res_prof["primary_language"], "Python")

        # Query GET /apps/{app_id}/descriptor
        res_desc = asyncio.run(get_descriptor("app-test-123", store=aie_store))
        self.assertEqual(res_desc["application_id"], "app-test-123")


if __name__ == "__main__":
    unittest.main()
