"""
JavaDetector — detects Java frameworks from pom.xml / build.gradle.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class JavaDetector:
    """Detect Java / Kotlin technology from Maven and Gradle build files."""

    # groupId substring → (category, tech_name)
    POM_GROUP_MAP: dict[str, tuple[str, str]] = {
        "org.springframework.boot": ("frameworks", "Spring Boot"),
        "org.springframework": ("frameworks", "Spring"),
        "org.hibernate": ("databases", "Hibernate"),
        "io.quarkus": ("frameworks", "Quarkus"),
        "io.micronaut": ("frameworks", "Micronaut"),
        "com.sparkjava": ("frameworks", "Spark"),
        "io.vertx": ("frameworks", "Vert.x"),
        "org.postgresql": ("databases", "PostgreSQL"),
        "mysql": ("databases", "MySQL"),
        "com.h2database": ("databases", "H2"),
        "org.mongodb": ("databases", "MongoDB"),
        "redis.clients": ("caches", "Redis"),
        "io.lettuce": ("caches", "Redis"),
        "org.apache.kafka": ("message_brokers", "Kafka"),
        "com.rabbitmq": ("message_brokers", "RabbitMQ"),
        "org.apache.activemq": ("message_brokers", "ActiveMQ"),
        "io.grpc": ("infrastructure", "gRPC"),
        "io.projectreactor": ("frameworks", "Project Reactor"),
    }

    # artifactId substring → (category, tech_name)
    POM_ARTIFACT_MAP: dict[str, tuple[str, str]] = {
        "spring-boot": ("frameworks", "Spring Boot"),
        "spring-web": ("frameworks", "Spring Web"),
        "spring-data": ("databases", "Spring Data"),
        "hibernate-core": ("databases", "Hibernate"),
        "postgresql": ("databases", "PostgreSQL"),
        "mysql-connector": ("databases", "MySQL"),
        "jedis": ("caches", "Redis"),
        "lettuce-core": ("caches", "Redis"),
        "kafka": ("message_brokers", "Kafka"),
        "spring-kafka": ("message_brokers", "Kafka"),
        "spring-rabbit": ("message_brokers", "RabbitMQ"),
        "amqp-client": ("message_brokers", "RabbitMQ"),
        "mybatis": ("databases", "MyBatis"),
        "flyway": ("databases", "Flyway"),
        "liquibase": ("databases", "Liquibase"),
        "tomcat": ("web_servers", "Tomcat"),
        "undertow": ("web_servers", "Undertow"),
        "netty": ("web_servers", "Netty"),
    }

    GRADLE_PATTERN_MAP: list[tuple[re.Pattern, str, str]] = [
        (re.compile(r"spring-boot", re.I), "frameworks", "Spring Boot"),
        (re.compile(r"spring-web", re.I), "frameworks", "Spring Web"),
        (re.compile(r"hibernate", re.I), "databases", "Hibernate"),
        (re.compile(r"postgresql", re.I), "databases", "PostgreSQL"),
        (re.compile(r"mysql", re.I), "databases", "MySQL"),
        (re.compile(r"jedis|lettuce", re.I), "caches", "Redis"),
        (re.compile(r"kafka", re.I), "message_brokers", "Kafka"),
        (re.compile(r"rabbitmq|amqp", re.I), "message_brokers", "RabbitMQ"),
        (re.compile(r"quarkus", re.I), "frameworks", "Quarkus"),
        (re.compile(r"micronaut", re.I), "frameworks", "Micronaut"),
        (re.compile(r"tomcat", re.I), "web_servers", "Tomcat"),
    ]

    ENTRY_POINT_MAP: dict[str, str] = {
        "Main.java": "main",
        "Application.java": "main",
        "App.java": "main",
        "Bootstrap.java": "main",
        "Main.kt": "main",
        "Application.kt": "main",
        "App.kt": "main",
    }

    def detect(self, workspace_root: Path, file_inventory: list[dict]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "frameworks": [],
            "databases": [],
            "caches": [],
            "message_brokers": [],
            "web_servers": [],
            "infrastructure": [],
            "dependency_managers": [],
            "entry_points": [],
        }
        seen: dict[str, set[str]] = {k: set() for k in result}

        pom_files = [item for item in file_inventory if Path(item["path"]).name == "pom.xml"]
        gradle_files = [item for item in file_inventory if Path(item["path"]).name in ("build.gradle", "build.gradle.kts")]

        if pom_files:
            mgr = "Maven"
            if mgr not in seen["dependency_managers"]:
                seen["dependency_managers"].add(mgr)
                result["dependency_managers"].append({"name": mgr, "detected_from": "pom.xml", "category": "dependency_manager"})

        if gradle_files:
            mgr = "Gradle"
            if mgr not in seen["dependency_managers"]:
                seen["dependency_managers"].add(mgr)
                result["dependency_managers"].append({"name": mgr, "detected_from": "build.gradle", "category": "dependency_manager"})

        for item in pom_files:
            self._parse_pom(workspace_root, item["path"], result, seen)

        for item in gradle_files:
            self._parse_gradle(workspace_root, item["path"], result, seen)

        # Entry points
        for item in file_inventory:
            fname = Path(item["path"]).name
            if fname in self.ENTRY_POINT_MAP:
                ep_type = self.ENTRY_POINT_MAP[fname]
                result["entry_points"].append({"file": item["path"], "type": ep_type})

        return result

    # ------------------------------------------------------------------

    def _parse_pom(self, workspace_root: Path, rel_path: str, result: dict, seen: dict) -> None:
        full_path = workspace_root / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
            # Strip namespace for simpler parsing
            text = re.sub(r' xmlns[^"]*"[^"]*"', "", text)
            root = ET.fromstring(text)
        except Exception:
            return

        ns = ""
        # Dependencies
        for dep in root.iter("dependency"):
            group_id = (dep.findtext("groupId") or "").strip()
            artifact_id = (dep.findtext("artifactId") or "").strip()
            version = (dep.findtext("version") or "").strip() or None

            for key, (cat, tech) in self.POM_GROUP_MAP.items():
                if key in group_id:
                    if tech not in seen[cat]:
                        seen[cat].add(tech)
                        result[cat].append({"name": tech, "version": version, "detected_from": "pom.xml", "category": cat})
                    break

            for key, (cat, tech) in self.POM_ARTIFACT_MAP.items():
                if key in artifact_id:
                    if tech not in seen[cat]:
                        seen[cat].add(tech)
                        result[cat].append({"name": tech, "version": version, "detected_from": "pom.xml", "category": cat})
                    break

    def _parse_gradle(self, workspace_root: Path, rel_path: str, result: dict, seen: dict) -> None:
        full_path = workspace_root / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        for pattern, cat, tech in self.GRADLE_PATTERN_MAP:
            if pattern.search(text):
                if tech not in seen[cat]:
                    seen[cat].add(tech)
                    result[cat].append({"name": tech, "version": None, "detected_from": rel_path, "category": cat})
