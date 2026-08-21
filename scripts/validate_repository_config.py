#!/usr/bin/env python3
"""Validate the production declmig evidence-promotion repository."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from verify_test_evidence import validate_manifest

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"lin_api_[A-Za-z0-9]{20,}"),
    re.compile(r"cfat_[A-Za-z0-9_-]{20,}"),
    re.compile(r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY"),
)


class ConfigError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(data, dict), f"{path} must contain a JSON object")
    return data


def walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in walk_strings(item)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            for text in (*walk_strings(key), *walk_strings(item))
        ]
    return []


def main() -> int:
    try:
        config_path = Path("config/repository.json")
        config = load_object(config_path)
        require(config.get("schema_version") == 2, "repository schema_version must be 2")
        require(
            config.get("repository") == "declarative-migrations/declmig-e2e",
            "unexpected aggregate repository",
        )
        require(
            config.get("mode") == "stable-promotion-orchestrator",
            "production mode mismatch",
        )
        require(
            config.get("source_repository")
            == "declarative-migrations/declarative-postgres-migrate.rs",
            "source repository mismatch",
        )
        require(
            config.get("evidence_producer")
            == "declarative-migrations-test/declmig-e2e",
            "test evidence producer mismatch",
        )
        require(
            config.get("destructive_targets_allowed") is False,
            "production aggregate cannot accept destructive targets",
        )
        require(
            config.get("database_targets_allowed") is False,
            "production aggregate cannot execute against database targets",
        )
        require(
            config.get("production_credentials_allowed") is False,
            "production credentials must be forbidden",
        )
        require(
            config.get("test_evidence_required_before_release") is True,
            "test evidence must precede release",
        )
        require(
            config.get("candidate_pull_request_evidence_allowed") is True,
            "candidate evidence policy mismatch",
        )
        require(
            config.get("release_evidence_requires_main_push") is True,
            "release evidence must require a test-main push",
        )
        require(
            config.get("required_jobs") == ["contract", "test-evidence"],
            "production required job set mismatch",
        )

        github_repository = os.environ.get("GITHUB_REPOSITORY")
        if github_repository:
            require(
                github_repository == config["repository"],
                f"config is for {config['repository']}, runner is {github_repository}",
            )

        source_pin_path = Path(str(config.get("source_pin_file")))
        source_pin = load_object(source_pin_path)
        require(source_pin.get("schema_version") == 1, "source pin schema_version must be 1")
        require(
            source_pin.get("source_repository") == config["source_repository"],
            "source pin repository mismatch",
        )
        source_commit = source_pin.get("source_commit")
        require(
            isinstance(source_commit, str)
            and SHA40.fullmatch(source_commit) is not None,
            "source_commit must be a full lowercase SHA",
        )

        evidence_pin_path = Path(str(config.get("evidence_pin_file")))
        evidence_pin = load_object(evidence_pin_path)
        validate_manifest(evidence_pin)
        require(
            evidence_pin["producer_repository"] == config["evidence_producer"],
            "evidence producer differs from repository policy",
        )
        require(
            evidence_pin["source_repository"] == source_pin["source_repository"],
            "evidence and local source repositories differ",
        )
        require(
            evidence_pin["source_commit"] == source_commit,
            "evidence and local source commits differ",
        )

        for text in (
            walk_strings(config)
            + walk_strings(source_pin)
            + walk_strings(evidence_pin)
        ):
            for pattern in SECRET_PATTERNS:
                require(
                    pattern.search(text) is None,
                    "configuration contains secret-like material",
                )

        summary = {
            "repository": config["repository"],
            "mode": config["mode"],
            "evidence_producer": evidence_pin["producer_repository"],
            "evidence_run_id": evidence_pin["run_id"],
            "evidence_head_sha": evidence_pin["head_sha"],
            "source_repository": source_pin["source_repository"],
            "source_commit": source_commit,
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "source_pin_sha256": hashlib.sha256(source_pin_path.read_bytes()).hexdigest(),
            "evidence_pin_sha256": hashlib.sha256(evidence_pin_path.read_bytes()).hexdigest(),
        }
        Path("artifacts").mkdir(exist_ok=True)
        Path("artifacts/config-validation.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ConfigError, ValueError) as exc:
        print(f"declmig repository config invalid: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
