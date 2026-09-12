#!/usr/bin/env python3
"""Verify immutable test-organization evidence before release promotion."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
EXPECTED_PRODUCER = "declarative-migrations-test/declmig-e2e"
EXPECTED_SOURCE = "declarative-migrations/declarative-postgres-migrate.rs"
EXPECTED_JOBS = (
    "contract",
    "postgres-smoke",
    "postgres-lease-invariant",
    "cockroach-smoke",
    "dual-engine-parity",
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
JsonFetcher = Callable[[str], Any]


class EvidenceError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(data, dict), f"{path} must contain a JSON object")
    return data


def require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    require(
        actual == expected,
        f"{label} keys differ: expected {sorted(expected)}, got {sorted(actual)}",
    )


def validate_manifest(manifest: dict[str, Any]) -> None:
    require_exact_keys(
        manifest,
        {
            "schema_version",
            "producer_repository",
            "workflow_id",
            "workflow_path",
            "run_id",
            "run_attempt",
            "event",
            "head_branch",
            "head_sha",
            "source_repository",
            "source_pin_path",
            "source_commit",
            "required_jobs",
            "artifacts",
        },
        "evidence manifest",
    )
    require(manifest["schema_version"] == 1, "evidence schema_version must be 1")
    require(
        manifest["producer_repository"] == EXPECTED_PRODUCER,
        "unexpected evidence producer",
    )
    require(
        manifest["source_repository"] == EXPECTED_SOURCE,
        "unexpected source repository",
    )
    require(
        manifest["workflow_path"] == ".github/workflows/e2e.yml",
        "unexpected workflow path",
    )
    require(
        manifest["source_pin_path"] == "pins/source.json",
        "unexpected source pin path",
    )
    require(
        isinstance(manifest["workflow_id"], int) and manifest["workflow_id"] > 0,
        "workflow_id must be positive",
    )
    require(
        isinstance(manifest["run_id"], int) and manifest["run_id"] > 0,
        "run_id must be positive",
    )
    require(
        isinstance(manifest["run_attempt"], int)
        and manifest["run_attempt"] > 0,
        "run_attempt must be positive",
    )
    require(
        manifest["event"] in {"pull_request", "push"},
        "evidence event must be pull_request or push",
    )
    require(
        isinstance(manifest["head_branch"], str) and manifest["head_branch"],
        "head_branch must be non-empty",
    )
    require(
        isinstance(manifest["head_sha"], str)
        and SHA40.fullmatch(manifest["head_sha"]) is not None,
        "head_sha must be a full lowercase SHA",
    )
    require(
        isinstance(manifest["source_commit"], str)
        and SHA40.fullmatch(manifest["source_commit"]) is not None,
        "source_commit must be a full lowercase SHA",
    )
    require(
        tuple(manifest["required_jobs"]) == EXPECTED_JOBS,
        "required test job set or order differs",
    )

    artifacts = manifest["artifacts"]
    require(
        isinstance(artifacts, list) and artifacts,
        "artifacts must be a non-empty list",
    )
    expected_names = {
        f"declmig-e2e-postgres-{manifest['run_id']}-{manifest['run_attempt']}",
        f"declmig-e2e-postgres-lease-{manifest['run_id']}-{manifest['run_attempt']}",
        f"declmig-e2e-cockroach-{manifest['run_id']}-{manifest['run_attempt']}",
        f"declmig-e2e-dual-engine-{manifest['run_id']}-{manifest['run_attempt']}",
    }
    names: set[str] = set()
    ids: set[int] = set()
    for index, artifact in enumerate(artifacts):
        require(isinstance(artifact, dict), f"artifact {index} must be an object")
        require_exact_keys(
            artifact,
            {"id", "name", "size_in_bytes", "digest"},
            f"artifact {index}",
        )
        require(
            isinstance(artifact["id"], int) and artifact["id"] > 0,
            f"artifact {index} id must be positive",
        )
        require(
            isinstance(artifact["name"], str),
            f"artifact {index} name must be text",
        )
        require(
            isinstance(artifact["size_in_bytes"], int)
            and artifact["size_in_bytes"] > 0,
            f"artifact {index} size must be positive",
        )
        require(
            isinstance(artifact["digest"], str)
            and SHA256_DIGEST.fullmatch(artifact["digest"]) is not None,
            f"artifact {index} digest must be sha256",
        )
        require(
            artifact["name"] not in names,
            f"duplicate artifact name {artifact['name']}",
        )
        require(
            artifact["id"] not in ids,
            f"duplicate artifact id {artifact['id']}",
        )
        names.add(artifact["name"])
        ids.add(artifact["id"])
    require(
        names == expected_names,
        f"artifact names differ: expected {sorted(expected_names)}, got {sorted(names)}",
    )


def request_json(url: str, token: str | None = None) -> Any:
    allowed_prefix = f"{API_ROOT}/repos/{EXPECTED_PRODUCER}/"
    require(
        url.startswith(allowed_prefix),
        "refusing an unexpected GitHub API origin or repository",
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "declmig-independent-evidence-verifier/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise EvidenceError(f"GitHub evidence request failed: {exc}") from exc
    require(
        len(payload) <= MAX_RESPONSE_BYTES,
        "GitHub evidence response exceeded the size limit",
    )
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EvidenceError("GitHub evidence response was not valid JSON") from exc


def decode_source_pin(payload: Any) -> dict[str, Any]:
    require(isinstance(payload, dict), "source pin response must be an object")
    require(
        payload.get("encoding") == "base64",
        "source pin response must use base64 encoding",
    )
    content = payload.get("content")
    require(isinstance(content, str), "source pin response is missing content")
    try:
        decoded = base64.b64decode(content, validate=False)
        pin = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise EvidenceError("source pin response could not be decoded") from exc
    require(isinstance(pin, dict), "source pin must contain an object")
    return pin


def verify_evidence(
    manifest: dict[str, Any],
    mode: str,
    fetch_json: JsonFetcher,
) -> dict[str, Any]:
    validate_manifest(manifest)
    require(
        mode in {"candidate", "release"},
        "verification mode must be candidate or release",
    )

    producer = manifest["producer_repository"]
    base = f"{API_ROOT}/repos/{producer}"
    run = fetch_json(f"{base}/actions/runs/{manifest['run_id']}")
    require(isinstance(run, dict), "workflow run response must be an object")
    require(run.get("id") == manifest["run_id"], "workflow run id mismatch")
    require(
        run.get("run_attempt") == manifest["run_attempt"],
        "workflow run attempt mismatch",
    )
    require(
        run.get("workflow_id") == manifest["workflow_id"],
        "workflow id mismatch",
    )
    require(
        run.get("path") == manifest["workflow_path"],
        "workflow path mismatch",
    )
    require(run.get("event") == manifest["event"], "workflow event mismatch")
    require(
        run.get("head_branch") == manifest["head_branch"],
        "workflow head branch mismatch",
    )
    require(
        run.get("head_sha") == manifest["head_sha"],
        "workflow head SHA mismatch",
    )
    require(run.get("status") == "completed", "workflow run is not completed")
    require(
        run.get("conclusion") == "success",
        "workflow run did not succeed",
    )
    require(
        run.get("repository", {}).get("full_name") == producer,
        "workflow repository mismatch",
    )
    require(
        run.get("head_repository", {}).get("full_name") == producer,
        "fork evidence is forbidden",
    )

    if mode == "release":
        require(
            manifest["event"] == "push",
            "release promotion requires push evidence",
        )
        require(
            manifest["head_branch"] == "main",
            "release promotion requires evidence from test main",
        )

    jobs_payload = fetch_json(
        f"{base}/actions/runs/{manifest['run_id']}/jobs?per_page=100"
    )
    require(
        isinstance(jobs_payload, dict),
        "workflow jobs response must be an object",
    )
    jobs = jobs_payload.get("jobs")
    require(isinstance(jobs, list), "workflow jobs response is missing jobs")
    require(
        jobs_payload.get("total_count") in {None, len(jobs)},
        "workflow jobs response was truncated",
    )
    require(
        len(jobs) == len(EXPECTED_JOBS),
        "workflow job count differs from the pinned contract",
    )
    jobs_by_name: dict[str, dict[str, Any]] = {}
    for job in jobs:
        require(isinstance(job, dict), "workflow job must be an object")
        name = job.get("name")
        require(
            isinstance(name, str) and name not in jobs_by_name,
            "workflow job names must be unique",
        )
        jobs_by_name[name] = job
    require(
        set(jobs_by_name) == set(EXPECTED_JOBS),
        "workflow job names differ from the pinned contract",
    )
    for name in EXPECTED_JOBS:
        job = jobs_by_name[name]
        require(
            job.get("run_id") in {None, manifest["run_id"]},
            f"job {name} run id mismatch",
        )
        require(
            job.get("status") == "completed",
            f"job {name} is not completed",
        )
        require(
            job.get("conclusion") == "success",
            f"job {name} did not succeed",
        )

    artifacts_payload = fetch_json(
        f"{base}/actions/runs/{manifest['run_id']}/artifacts?per_page=100"
    )
    require(
        isinstance(artifacts_payload, dict),
        "artifact response must be an object",
    )
    artifacts = artifacts_payload.get("artifacts")
    require(isinstance(artifacts, list), "artifact response is missing artifacts")
    require(
        artifacts_payload.get("total_count") in {None, len(artifacts)},
        "artifact response was truncated",
    )
    expected_artifacts = {item["name"]: item for item in manifest["artifacts"]}
    actual_artifacts: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        require(isinstance(artifact, dict), "workflow artifact must be an object")
        name = artifact.get("name")
        require(
            isinstance(name, str) and name not in actual_artifacts,
            "workflow artifact names must be unique",
        )
        actual_artifacts[name] = artifact
    require(
        set(actual_artifacts) == set(expected_artifacts),
        "workflow artifact names differ from the evidence manifest",
    )
    for name, expected in expected_artifacts.items():
        actual = actual_artifacts[name]
        require(actual.get("id") == expected["id"], f"artifact {name} id mismatch")
        require(
            actual.get("size_in_bytes") == expected["size_in_bytes"],
            f"artifact {name} size mismatch",
        )
        require(
            actual.get("digest") == expected["digest"],
            f"artifact {name} digest mismatch",
        )
        require(actual.get("expired") is False, f"artifact {name} is expired")
        workflow_run = actual.get("workflow_run", {})
        require(
            workflow_run.get("id") in {None, manifest["run_id"]},
            f"artifact {name} run id mismatch",
        )
        require(
            workflow_run.get("head_sha") in {None, manifest["head_sha"]},
            f"artifact {name} head SHA mismatch",
        )

    pin_path = quote(manifest["source_pin_path"], safe="/")
    pin_payload = fetch_json(
        f"{base}/contents/{pin_path}?ref={manifest['head_sha']}"
    )
    source_pin = decode_source_pin(pin_payload)
    require(
        source_pin.get("source_repository") == manifest["source_repository"],
        "producer source repository pin mismatch",
    )
    require(
        source_pin.get("source_commit") == manifest["source_commit"],
        "producer source commit pin mismatch",
    )

    return {
        "mode": mode,
        "producer_repository": producer,
        "workflow_id": manifest["workflow_id"],
        "run_id": manifest["run_id"],
        "run_attempt": manifest["run_attempt"],
        "event": manifest["event"],
        "head_branch": manifest["head_branch"],
        "head_sha": manifest["head_sha"],
        "source_repository": manifest["source_repository"],
        "source_commit": manifest["source_commit"],
        "jobs": list(EXPECTED_JOBS),
        "artifacts": [
            {
                "name": item["name"],
                "id": item["id"],
                "digest": item["digest"],
            }
            for item in manifest["artifacts"]
        ],
        "result": "candidate-passed" if mode == "candidate" else "release-passed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("pins/test-evidence.json"),
    )
    parser.add_argument(
        "--mode",
        choices=("candidate", "release"),
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = load_object(args.manifest)
        local_source_pin = load_object(Path("pins/source.json"))
        validate_manifest(manifest)
        require(
            local_source_pin.get("source_repository")
            == manifest["source_repository"],
            "local source repository pin mismatch",
        )
        require(
            local_source_pin.get("source_commit") == manifest["source_commit"],
            "local source commit pin mismatch",
        )
        token = os.environ.get("GITHUB_TOKEN")
        summary = verify_evidence(
            manifest,
            args.mode,
            lambda url: request_json(url, token=token),
        )
        summary["manifest_sha256"] = hashlib.sha256(
            args.manifest.read_bytes()
        ).hexdigest()
        Path("artifacts").mkdir(exist_ok=True)
        Path("artifacts/test-evidence-verification.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, EvidenceError) as exc:
        print(f"independent test evidence invalid: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
