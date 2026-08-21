from __future__ import annotations

import base64
import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_test_evidence as verifier  # noqa: E402


class EvidenceFixture:
    def __init__(self) -> None:
        self.manifest = json.loads(
            (ROOT / "pins" / "test-evidence.json").read_text(encoding="utf-8")
        )
        manifest = self.manifest
        self.run: dict[str, Any] = {
            "id": manifest["run_id"],
            "run_attempt": manifest["run_attempt"],
            "workflow_id": manifest["workflow_id"],
            "path": manifest["workflow_path"],
            "event": manifest["event"],
            "head_branch": manifest["head_branch"],
            "head_sha": manifest["head_sha"],
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": manifest["producer_repository"]},
            "head_repository": {"full_name": manifest["producer_repository"]},
        }
        self.jobs: dict[str, Any] = {
            "total_count": len(manifest["required_jobs"]),
            "jobs": [
                {
                    "name": name,
                    "run_id": manifest["run_id"],
                    "status": "completed",
                    "conclusion": "success",
                }
                for name in manifest["required_jobs"]
            ],
        }
        self.artifacts: dict[str, Any] = {
            "total_count": len(manifest["artifacts"]),
            "artifacts": [
                {
                    **artifact,
                    "expired": False,
                    "workflow_run": {
                        "id": manifest["run_id"],
                        "head_sha": manifest["head_sha"],
                    },
                }
                for artifact in manifest["artifacts"]
            ],
        }
        source_pin = {
            "schema_version": 1,
            "source_repository": manifest["source_repository"],
            "source_commit": manifest["source_commit"],
        }
        self.source_pin: dict[str, Any] = {
            "encoding": "base64",
            "content": base64.b64encode(json.dumps(source_pin).encode()).decode(),
        }

    def fetch(self, url: str) -> Any:
        run_id = self.manifest["run_id"]
        if url.endswith(f"/actions/runs/{run_id}/jobs?per_page=100"):
            return copy.deepcopy(self.jobs)
        if url.endswith(f"/actions/runs/{run_id}/artifacts?per_page=100"):
            return copy.deepcopy(self.artifacts)
        if url.endswith(f"/actions/runs/{run_id}"):
            return copy.deepcopy(self.run)
        if "/contents/pins/source.json?ref=" in url:
            return copy.deepcopy(self.source_pin)
        raise AssertionError(f"unexpected URL {url}")


class VerifyTestEvidenceTests(unittest.TestCase):
    def test_candidate_accepts_exact_pull_request_evidence(self) -> None:
        fixture = EvidenceFixture()
        summary = verifier.verify_evidence(
            fixture.manifest, "candidate", fixture.fetch
        )
        self.assertEqual(summary["result"], "candidate-passed")
        self.assertEqual(summary["source_commit"], fixture.manifest["source_commit"])

    def test_release_rejects_pull_request_evidence(self) -> None:
        fixture = EvidenceFixture()
        with self.assertRaisesRegex(verifier.EvidenceError, "requires push evidence"):
            verifier.verify_evidence(fixture.manifest, "release", fixture.fetch)

    def test_release_accepts_exact_main_push_evidence(self) -> None:
        fixture = EvidenceFixture()
        fixture.manifest["event"] = "push"
        fixture.manifest["head_branch"] = "main"
        fixture.run["event"] = "push"
        fixture.run["head_branch"] = "main"
        summary = verifier.verify_evidence(
            fixture.manifest, "release", fixture.fetch
        )
        self.assertEqual(summary["result"], "release-passed")

    def test_failed_required_job_is_rejected(self) -> None:
        fixture = EvidenceFixture()
        fixture.jobs["jobs"][2]["conclusion"] = "failure"
        with self.assertRaisesRegex(verifier.EvidenceError, "did not succeed"):
            verifier.verify_evidence(fixture.manifest, "candidate", fixture.fetch)

    def test_artifact_digest_drift_is_rejected(self) -> None:
        fixture = EvidenceFixture()
        fixture.artifacts["artifacts"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(verifier.EvidenceError, "digest mismatch"):
            verifier.verify_evidence(fixture.manifest, "candidate", fixture.fetch)

    def test_source_pin_drift_is_rejected(self) -> None:
        fixture = EvidenceFixture()
        source_pin = {
            "schema_version": 1,
            "source_repository": fixture.manifest["source_repository"],
            "source_commit": "0" * 40,
        }
        fixture.source_pin["content"] = base64.b64encode(
            json.dumps(source_pin).encode()
        ).decode()
        with self.assertRaisesRegex(verifier.EvidenceError, "source commit pin mismatch"):
            verifier.verify_evidence(fixture.manifest, "candidate", fixture.fetch)

    def test_fork_evidence_is_rejected(self) -> None:
        fixture = EvidenceFixture()
        fixture.run["head_repository"] = {"full_name": "attacker/declmig-e2e"}
        with self.assertRaisesRegex(verifier.EvidenceError, "fork evidence is forbidden"):
            verifier.verify_evidence(fixture.manifest, "candidate", fixture.fetch)


if __name__ == "__main__":
    unittest.main()
