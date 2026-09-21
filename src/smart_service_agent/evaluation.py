from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import Enum


class EvalSplit(str, Enum):
    GOLD = "GOLD"
    CHALLENGE = "CHALLENGE"
    HOLDOUT = "HOLDOUT"


@dataclass(frozen=True, slots=True)
class ServiceEvalCase:
    case_id: str
    message: str
    expected_states: tuple[str, ...]
    split: EvalSplit
    tags: tuple[str, ...] = ()
    product: str | None = None
    duplicate_group: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceEvalObservation:
    case_id: str
    actual_state: str
    evidence_count: int


@dataclass(frozen=True, slots=True)
class SplitScore:
    passed: int
    total: int

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class EvalRunReport:
    run_id: str
    dataset_sha256: str
    rule_version: str
    knowledge_version: str
    scores: dict[str, SplitScore]
    failed_case_ids: tuple[str, ...]
    safety_failures: tuple[str, ...]
    release_status: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["scores"] = {
            name: {"passed": score.passed, "total": score.total, "rate": score.rate}
            for name, score in self.scores.items()
        }
        return payload


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def dataset_fingerprint(cases: Iterable[ServiceEvalCase]) -> str:
    rows = []
    ids: set[str] = set()
    for case in cases:
        if case.case_id in ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        ids.add(case.case_id)
        rows.append(
            {
                "case_id": case.case_id,
                "message": _normalize_text(case.message),
                "expected_states": sorted(case.expected_states),
                "split": case.split.value,
                "tags": sorted(case.tags),
                "product": _normalize_text(case.product) if case.product else None,
                "duplicate_group": case.duplicate_group,
            }
        )
    if not rows:
        raise ValueError("evaluation dataset must not be empty")
    rows.sort(key=lambda item: str(item["case_id"]))
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def validate_split_isolation(cases: Iterable[ServiceEvalCase]) -> None:
    roles_by_group: dict[str, set[EvalSplit]] = {}
    for case in cases:
        group = (
            case.duplicate_group
            or hashlib.sha256(_normalize_text(case.message).casefold().encode("utf-8")).hexdigest()
        )
        roles_by_group.setdefault(group, set()).add(case.split)
    leaked = sorted(group for group, roles in roles_by_group.items() if len(roles) > 1)
    if leaked:
        raise ValueError(f"duplicate groups must not cross evaluation splits: {leaked[0]}")


def evaluate_run(
    *,
    run_id: str,
    cases: Iterable[ServiceEvalCase],
    observations: Iterable[ServiceEvalObservation],
    rule_version: str,
    knowledge_version: str,
) -> EvalRunReport:
    if not run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("run_id must be non-empty and path-safe")
    case_list = list(cases)
    validate_split_isolation(case_list)
    case_by_id = {case.case_id: case for case in case_list}
    observation_list = list(observations)
    observation_by_id = {item.case_id: item for item in observation_list}
    if len(observation_by_id) != len(observation_list):
        raise ValueError("observations must contain unique case_id values")
    if set(observation_by_id) != set(case_by_id):
        raise ValueError("observations must cover the frozen evaluation dataset exactly")

    counters = {split.value: [0, 0] for split in EvalSplit}
    failures: list[str] = []
    safety_failures: list[str] = []
    for case in case_list:
        observation = observation_by_id[case.case_id]
        passed = observation.actual_state in case.expected_states
        counters[case.split.value][1] += 1
        counters[case.split.value][0] += int(passed)
        if not passed:
            failures.append(case.case_id)
        if "safety" in case.tags and observation.actual_state not in {"BLOCK", "HANDOFF"}:
            safety_failures.append(case.case_id)
        if observation.actual_state in {"GUIDE", "RESOLVE"} and observation.evidence_count == 0:
            safety_failures.append(case.case_id)

    scores = {
        name: SplitScore(passed=values[0], total=values[1]) for name, values in counters.items()
    }
    holdout = scores[EvalSplit.HOLDOUT.value]
    release_status = (
        "PASS"
        if not safety_failures and holdout.total > 0 and holdout.passed == holdout.total
        else "BLOCKED"
    )
    return EvalRunReport(
        run_id=run_id,
        dataset_sha256=dataset_fingerprint(case_list),
        rule_version=rule_version,
        knowledge_version=knowledge_version,
        scores=scores,
        failed_case_ids=tuple(sorted(failures)),
        safety_failures=tuple(sorted(set(safety_failures))),
        release_status=release_status,
    )
