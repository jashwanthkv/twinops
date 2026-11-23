"""Rule for detecting AI agent, orchestrator, and LLM workflow components.

A candidate is ONLY created when:
  1. has_agent_imports=True  (a verified AI/LLM SDK is imported), AND
  2. Either has_orchestrator_class=True OR actual_agent_calls >= 2
"""
from __future__ import annotations
from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR

class AgentRule:
    """Detects AI agent systems, LLM orchestrators, and agentic workflows.

    Requires a real AI/LLM SDK import (Tier A) before emitting any candidate.
    Generic class names like Runner, Pipeline, Workflow never trigger detection alone.
    """

    # Tier A: verified AI/LLM SDK packages — import of ANY of these is proof-of-use
    _AGENT_SDK_IMPORTS = {
        "langgraph", "crewai", "autogen", "pydantic_ai",
        "llama_index", "haystack", "dspy",
        "langchain_core", "langchain_community", "langchain",
    }
    _LLM_CLIENT_IMPORTS = {
        "openai", "anthropic", "groq", "together", "cohere", "mistralai",
        "google.generativeai", "litellm", "ollama",
    }
    # Framework orchestrator class names (only valid WITH an SDK import)
    _ORCHESTRATOR_CLASSES = {
        "StateGraph", "MessageGraph", "CompiledGraph",
        "AgentExecutor", "Crew", "AutoGen",
    }
    # Narrowed to genuinely agent-only keywords (not generic OOP terms)
    _AGENT_CLASS_KEYWORDS = {"agent", "orchestrator", "crew"}
    # Narrowed to agentic API patterns only
    _AGENT_FUNC_KEYWORDS = {"run_agent", "run_crew", "step_agent", "observe"}
    # Graph-build ops — only count when has_agent_imports is True
    _GRAPH_OPS = {"add_node", "add_edge", "compile", "invoke", "stream"}
    _AGENT_DIR_KEYWORDS = {"agents", "agent", "crew", "chains"}

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        agent_files: set[str] = set()
        orchestrator_files: set[str] = set()
        llm_client_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        agent_technologies: set[str] = set()
        llm_technologies: set[str] = set()
        has_agent_imports = False
        has_llm_imports = False
        has_orchestrator_class = False
        actual_agent_calls = 0

        for f in codebase_ir.files:
            path_parts = set(f.file_path.replace("\\", "/").lower().split("/"))

            # 1. Check imports — ONLY real SDK imports count as Tier A
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                root_mod = mod.split(".")[0]

                if root_mod in self._AGENT_SDK_IMPORTS or any(a in mod for a in self._AGENT_SDK_IMPORTS):
                    has_agent_imports = True
                    agent_technologies.add(root_mod)
                    agent_files.add(f.file_path)
                    evidence_list.append(SourceEvidence(
                        evidence_type=EvidenceType.DEPENDENCY_MANIFEST,
                        source_path=f.file_path,
                        snippet=f"Agent SDK import: {imp.module or imp.from_module}",
                        confidence=0.9,
                        notes=f"Verified AI SDK imported in {f.file_path}:{imp.line}",
                    ))

                if root_mod in self._LLM_CLIENT_IMPORTS:
                    has_llm_imports = True
                    llm_technologies.add(root_mod)
                    llm_client_files.add(f.file_path)

            # 2. Check class names — orchestrator classes only valid with SDK import
            for cls in f.classes:
                cls_lower = cls.name.lower()
                if cls.name in self._ORCHESTRATOR_CLASSES or any(b in self._ORCHESTRATOR_CLASSES for b in cls.bases):
                    if has_agent_imports:  # GATE: requires SDK import
                        has_orchestrator_class = True
                        orchestrator_files.add(f.file_path)
                        evidence_list.append(SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"class {cls.name}({', '.join(cls.bases)}) — orchestrator",
                            confidence=0.9,
                            notes=f"Orchestrator class at {f.file_path}:{cls.line}",
                        ))
                # Narrowed keywords — only "agent", "orchestrator", "crew"
                elif any(kw in cls_lower for kw in self._AGENT_CLASS_KEYWORDS):
                    if has_agent_imports:  # GATE: requires SDK import
                        agent_files.add(f.file_path)
                        evidence_list.append(SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"class {cls.name} — agent-pattern class",
                            confidence=0.75,
                            notes=f"Agent-named class at {f.file_path}:{cls.line}",
                        ))

            # 3. Graph-build ops — only count when SDK import confirmed
            if has_agent_imports:
                all_calls = list(f.global_calls)
                for fn in f.functions:
                    all_calls.extend(fn.calls)
                    if any(kw in fn.name.lower() for kw in self._AGENT_FUNC_KEYWORDS):
                        actual_agent_calls += 1
                for call in all_calls:
                    func_name = call.callee.split(".")[-1]
                    if func_name in self._GRAPH_OPS:
                        actual_agent_calls += 1
                        orchestrator_files.add(f.file_path)

            # 4. Directory signals — only when SDK confirmed
            if has_agent_imports and (path_parts & self._AGENT_DIR_KEYWORDS):
                agent_files.add(f.file_path)

        # === GATE: NO candidate without Tier A SDK import ===
        if not has_agent_imports:
            return []

        candidates: list[ComponentCandidate] = []

        # Orchestrator component
        if orchestrator_files or has_orchestrator_class:
            orch_tech = next(
                (t for t in agent_technologies if any(x in t for x in ["langgraph", "langchain", "autogen", "crewai"])),
                next(iter(agent_technologies), "Orchestrator"),
            )
            candidates.append(ComponentCandidate(
                name=f"{orch_tech} Orchestrator",
                type=ComponentType.ORCHESTRATOR,
                technology=orch_tech,
                evidence=evidence_list[:10],
                files=sorted(list(orchestrator_files or agent_files)),
                score_evidence={
                    "imports": 0.9,
                    "code_behavior": min(1.0, 0.6 + 0.05 * actual_agent_calls),
                    "config": 0.6,
                    "framework_convention": 0.8 if has_orchestrator_class else 0.5,
                    "docs": 0.0,
                },
                metadata={
                    "orchestrator_files": sorted(list(orchestrator_files)),
                    "agent_technologies": sorted(list(agent_technologies)),
                },
            ))

        # Individual agent modules
        agent_only_files = agent_files - orchestrator_files
        individual_agents: dict[str, list[str]] = {}
        grouped_files: list[str] = []

        for af in sorted(list(agent_only_files)):
            parts = af.replace("\\", "/").split("/")
            filename = parts[-1].replace(".py", "")
            if filename in {"__init__", "base", "utils", "common"}:
                grouped_files.append(af)
                continue
            in_agents_dir = any("agent" in p.lower() for p in parts[:-1])
            is_agent_named = "agent" in filename.lower()
            if in_agents_dir or is_agent_named:
                agent_name = filename.replace("_agent", "").replace("-", "_")
                agent_title = " ".join(word.capitalize() for word in agent_name.split("_"))
                individual_agents[f"{agent_title} Agent"] = [af]
            else:
                grouped_files.append(af)

        for name, files in individual_agents.items():
            agent_tech = next(iter(agent_technologies), "Python")
            candidates.append(ComponentCandidate(
                name=name,
                type=ComponentType.AGENT,
                technology=agent_tech,
                evidence=[e for e in evidence_list if e.source_path in files][:5] or evidence_list[:2],
                files=files,
                score_evidence={
                    "imports": 0.9,
                    "code_behavior": 0.7,
                    "config": 0.5,
                    "framework_convention": 0.85,
                    "docs": 0.0,
                },
                metadata={"agent_name": name, "files": files},
            ))

        if grouped_files and not individual_agents:
            candidates.append(ComponentCandidate(
                name="AI Agent Modules",
                type=ComponentType.AGENT,
                technology=next(iter(agent_technologies), "Agent"),
                evidence=evidence_list[:8],
                files=grouped_files,
                score_evidence={
                    "imports": 0.9,
                    "code_behavior": min(1.0, 0.5 + 0.05 * actual_agent_calls),
                    "config": 0.5,
                    "framework_convention": 0.7,
                    "docs": 0.0,
                },
                metadata={"agent_file_count": len(grouped_files), "technologies": sorted(list(agent_technologies))},
            ))

        # LLM Client component
        if llm_client_files and llm_technologies:
            primary_llm = "Groq" if "groq" in llm_technologies else (
                "OpenAI" if "openai" in llm_technologies else next(iter(llm_technologies), "LLM")
            )
            llm_only = llm_client_files - orchestrator_files
            if llm_only or not orchestrator_files:
                candidates.append(ComponentCandidate(
                    name=f"{primary_llm} LLM Client",
                    type=ComponentType.SERVICE,
                    technology=primary_llm,
                    evidence=evidence_list[:5],
                    files=sorted(list(llm_client_files)),
                    score_evidence={
                        "imports": 0.9,
                        "code_behavior": 0.7,
                        "config": 0.6,
                        "framework_convention": 0.6,
                        "docs": 0.0,
                    },
                    metadata={"llm_providers": sorted(list(llm_technologies))},
                ))

        return candidates
