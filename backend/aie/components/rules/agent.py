"""Rule for detecting AI agent, orchestrator, and LLM workflow components."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class AgentRule:
    """Detects AI agent systems, LLM orchestrators, and agentic workflows.

    Signals: LangGraph StateGraph, CrewAI, AutoGen, LangChain agents, agent class names,
    agent directory structure, invocation patterns like invoke(), run_agent(), plan().
    """

    _AGENT_IMPORTS = {
        "langgraph", "langchain", "crewai", "autogen", "pydantic_ai",
        "openai", "anthropic", "groq", "together", "litellm", "ollama",
        "llama_index", "llama-index", "haystack", "dspy",
    }
    _LLM_CLIENT_IMPORTS = {
        "openai", "anthropic", "groq", "together", "cohere", "mistralai",
        "google.generativeai", "litellm",
    }
    _ORCHESTRATOR_CLASSES = {
        "StateGraph", "Graph", "MessageGraph", "CompiledGraph",
        "AgentExecutor", "Chain", "Pipeline", "Crew", "Flow",
    }
    _AGENT_CLASS_KEYWORDS = {
        "agent", "orchestrator", "runner", "planner", "executor", "workflow",
        "crew", "graph", "chain", "pipeline",
    }
    _AGENT_FUNC_KEYWORDS = {
        "run_agent", "invoke", "execute_step", "plan", "act", "observe",
        "run_graph", "compile", "add_node", "add_edge", "run_crew",
    }
    _AGENT_DIR_KEYWORDS = {"agents", "agent", "graph", "chains", "crew", "workflows"}

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
            file_path_lower = f.file_path.replace("\\", "/").lower()
            path_parts = set(file_path_lower.replace("\\", "/").split("/"))

            # 1. Check imports for agent/LLM frameworks
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                root_mod = mod.split(".")[0]

                if root_mod in self._AGENT_IMPORTS or any(a in mod for a in self._AGENT_IMPORTS):
                    has_agent_imports = True
                    agent_technologies.add(root_mod)
                    agent_files.add(f.file_path)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"Agent/LLM import: {imp.module or imp.from_module}",
                            confidence=0.8,
                            notes=f"AI/agent framework imported in {f.file_path}:{imp.line}",
                        )
                    )

                if root_mod in self._LLM_CLIENT_IMPORTS:
                    has_llm_imports = True
                    llm_technologies.add(root_mod)
                    llm_client_files.add(f.file_path)

            # 2. Check class names for agent/orchestrator patterns
            for cls in f.classes:
                cls_lower = cls.name.lower()
                # Check orchestrator framework classes
                if cls.name in self._ORCHESTRATOR_CLASSES or any(b in self._ORCHESTRATOR_CLASSES for b in cls.bases):
                    has_orchestrator_class = True
                    orchestrator_files.add(f.file_path)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"class {cls.name}({', '.join(cls.bases)}) — orchestrator pattern",
                            confidence=0.9,
                            notes=f"Orchestrator class at {f.file_path}:{cls.line}",
                        )
                    )
                # Check agent-named classes
                elif any(kw in cls_lower for kw in self._AGENT_CLASS_KEYWORDS):
                    agent_files.add(f.file_path)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"class {cls.name} — agent-pattern class",
                            confidence=0.75,
                            notes=f"Agent-named class at {f.file_path}:{cls.line}",
                        )
                    )

            # 3. Check function calls for agentic patterns
            all_calls = list(f.global_calls)
            for fn in f.functions:
                all_calls.extend(fn.calls)
                # Check function names
                if any(kw in fn.name.lower() for kw in self._AGENT_FUNC_KEYWORDS):
                    actual_agent_calls += 1
                    agent_files.add(f.file_path)

            for call in all_calls:
                callee_parts = call.callee.split(".")
                func_name = callee_parts[-1]
                # LangGraph: StateGraph().add_node, .add_edge, .compile
                if func_name in {"add_node", "add_edge", "compile", "invoke", "stream"}:
                    actual_agent_calls += 1
                    orchestrator_files.add(f.file_path)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"Orchestrator call: {call.callee}(...) at line {call.line}",
                            confidence=0.85,
                            notes=f"Graph/agent operation at {f.file_path}:{call.line}",
                        )
                    )

            # 4. Directory signals — agents/, graph/ directories
            if path_parts & self._AGENT_DIR_KEYWORDS:
                agent_files.add(f.file_path)

        # Build candidates
        candidates: list[ComponentCandidate] = []

        # Orchestrator component
        if orchestrator_files or has_orchestrator_class:
            orch_tech = next(
                (t for t in agent_technologies if any(x in t for x in ["langgraph", "langchain", "autogen", "crewai"])),
                "LangGraph" if "langgraph" in agent_technologies else "Orchestrator"
            )
            candidates.append(ComponentCandidate(
                name=f"{orch_tech} Orchestrator",
                type=ComponentType.ORCHESTRATOR,
                technology=orch_tech,
                evidence=evidence_list[:10],
                files=sorted(list(orchestrator_files or agent_files)),
                score_evidence={
                    "imports": 0.9 if has_agent_imports else 0.5,
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

        # Individual agent modules (e.g. Fetch Agent, Analyse Agent, Explain Agent)
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
                    "imports": 0.85 if has_agent_imports else 0.5,
                    "code_behavior": 0.7,
                    "config": 0.5,
                    "framework_convention": 0.85,
                    "docs": 0.0,
                },
                metadata={
                    "agent_name": name,
                    "files": files,
                },
            ))

        if grouped_files and not individual_agents:
            candidates.append(ComponentCandidate(
                name="AI Agent Modules",
                type=ComponentType.AGENT,
                technology=next(iter(agent_technologies), "Agent"),
                evidence=evidence_list[:8],
                files=grouped_files,
                score_evidence={
                    "imports": 0.85 if has_agent_imports else 0.5,
                    "code_behavior": min(1.0, 0.5 + 0.05 * actual_agent_calls),
                    "config": 0.5,
                    "framework_convention": 0.7,
                    "docs": 0.0,
                },
                metadata={
                    "agent_file_count": len(grouped_files),
                    "technologies": sorted(list(agent_technologies)),
                },
            ))

        # LLM Client component — separate from orchestrator
        if llm_client_files and llm_technologies:
            primary_llm = "Groq" if "groq" in llm_technologies else (
                "OpenAI" if "openai" in llm_technologies else next(iter(llm_technologies), "LLM")
            )
            # Only create if significantly different files from orchestrator
            llm_only = llm_client_files - orchestrator_files
            if llm_only or not orchestrator_files:
                candidates.append(ComponentCandidate(
                    name=f"{primary_llm} LLM Client",
                    type=ComponentType.SERVICE,
                    technology=primary_llm,
                    evidence=evidence_list[:5],
                    files=sorted(list(llm_client_files)),
                    score_evidence={
                        "imports": 0.9 if has_llm_imports else 0.4,
                        "code_behavior": 0.7,
                        "config": 0.6,
                        "framework_convention": 0.6,
                        "docs": 0.0,
                    },
                    metadata={
                        "llm_providers": sorted(list(llm_technologies)),
                    },
                ))

        return candidates
