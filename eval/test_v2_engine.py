"""End-to-end unit test for AIE v2 Architecture Discovery Engine."""
import sys
import unittest
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from aie.evidence.models import Evidence, EvidenceBundle
from aie.evidence.manifest_extractor import extract_from_requirements
from aie.evidence.catalog_resolver import CatalogResolver
from aie.components.candidates import form_candidates
from aie.components.verifier import Verifier
from aie.flows.builder import FlowBuilder
from aie.static_analysis.ir import CodebaseIR, FileIR, FunctionInfo, ImportInfo, CallInfo


class TestAIEv2Engine(unittest.TestCase):
    def test_fastapi_boilerplate_discovery(self):
        # 1. Simulate requirements.txt for a FastAPI boilerplate app
        req_content = """
fastapi==0.109.0
uvicorn==0.27.0
sqlalchemy==2.0.25
asyncpg==0.29.0
redis==5.0.1
python-jose==3.3.0
passlib==1.7.4
slowapi==0.1.9
pydantic==2.5.3
"""
        req_evidence = extract_from_requirements("requirements.txt", req_content)
        self.assertGreater(len(req_evidence), 5)

        bundle = EvidenceBundle()
        for ev in req_evidence:
            bundle.add(ev)

        # 2. Resolve against catalog
        resolver = CatalogResolver()
        tech_matches = resolver.resolve(bundle)
        tech_ids = {t.tech_id for t in tech_matches}

        self.assertIn("fastapi", tech_ids)
        self.assertIn("redis", tech_ids)
        self.assertIn("postgresql", tech_ids)
        self.assertIn("jwt", tech_ids)
        self.assertIn("slowapi", tech_ids)

        # Ensure NO AI or Vector Store catalog match
        self.assertNotIn("chromadb", tech_ids)
        self.assertNotIn("pinecone", tech_ids)
        self.assertNotIn("langchain", tech_ids)
        self.assertNotIn("langgraph", tech_ids)

        # 3. Simulate CodebaseIR
        file_routes = FileIR(
            file_path="app/api/v1/routes/users.py",
            language="python",
            imports=[
                ImportInfo(module="fastapi", names=["APIRouter", "Depends"], line=1),
                ImportInfo(module="app.core.auth", names=["get_current_user"], line=2),
            ],
            functions=[
                FunctionInfo(
                    name="read_users",
                    file="app/api/v1/routes/users.py",
                    line=10,
                    end_line=20,
                    calls=[CallInfo(callee="router.get", line=9, file="app/api/v1/routes/users.py")],
                )
            ],
            global_calls=[CallInfo(callee="router.get", line=9, file="app/api/v1/routes/users.py")],
        )

        file_auth = FileIR(
            file_path="app/core/auth.py",
            language="python",
            imports=[
                ImportInfo(module="jose", names=["jwt"], line=1),
                ImportInfo(module="passlib.context", names=["CryptContext"], line=2),
            ],
            functions=[
                FunctionInfo(name="get_current_user", file="app/core/auth.py", line=15, end_line=25, calls=[])
            ],
        )

        codebase_ir = CodebaseIR(
            repo_root="temp",
            files=[file_routes, file_auth],
        )

        # 4. Form candidates
        formation = form_candidates(codebase_ir, bundle, tech_matches)
        candidate_names = [c.name for c in formation.candidates]
        candidate_kinds = [c.kind for c in formation.candidates]

        self.assertIn("Authentication", candidate_names)
        self.assertIn("API Routing", candidate_names)
        self.assertIn("Redis Cache", candidate_names)
        self.assertIn("PostgreSQL Database", candidate_names)
        self.assertIn("Rate Limiting", candidate_names)

        # CRITICAL: Verify ZERO false-positive vector store or agent candidates
        self.assertNotIn("vector_store", candidate_kinds)
        self.assertNotIn("ai_agent", candidate_kinds)
        self.assertNotIn("agent", candidate_kinds)

        # 5. Verify components
        verifier = Verifier()
        comp_dicts = [
            {
                "name": c.name,
                "layer": c.layer,
                "kind": c.kind,
                "responsibility": f"{c.name} testing",
                "technologies": c.technologies,
                "confidence": c.confidence,
                "files": c.files,
                "evidence_ids": c.evidence_ids,
            }
            for c in formation.candidates
        ]
        ev_ids = {e.id for e in bundle.items}
        verification = verifier.verify(comp_dicts, ev_ids)
        self.assertEqual(len(verification.rejected), 0)
        self.assertEqual(len(verification.accepted), len(formation.candidates))

        # 6. Build deterministic flows
        flow_builder = FlowBuilder()
        flows = flow_builder.build(codebase_ir, formation.tech_nodes)
        self.assertGreaterEqual(len(flows), 2)  # public and authenticated flows

        auth_flow = next(f for f in flows if f.archetype == "authenticated")
        step_kinds = [s.kind for s in auth_flow.steps]
        self.assertIn("auth", step_kinds)
        self.assertIn("rate_limit", step_kinds)
        self.assertIn("handler", step_kinds)
        self.assertIn("cache", step_kinds)
        self.assertIn("database", step_kinds)

        print("\nAll v2 Discovery Engine unit tests passed successfully!")


if __name__ == "__main__":
    unittest.main()
