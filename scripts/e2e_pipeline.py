#!/usr/bin/env python3
"""End-to-end smoke test: one citizen submission must travel Modules 1 -> 4.

This is the only test that proves the modules are actually connected. It talks
to the running services over HTTP and Redis -- no imports between modules, no
direct database writes -- and asserts that a submission to Module 1 ends up in
Module 4's ``citizen_demand`` aggregate *and* in ``civic_record_lineage`` with
its ``request_id`` intact.

Usage:
    ./scripts/start-all.sh          # once, in another shell
    python3 scripts/e2e_pipeline.py

Optional flags:
    --text "..."        the citizen's complaint (default: a water complaint)
    --keep              leave the seeded mesh in place afterwards
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

MODULE1 = "http://127.0.0.1:8001/api/v1"
MODULE2 = "http://127.0.0.1:8002/api/v1"
MODULE3 = "http://127.0.0.1:8003/api/v1"
MODULE4 = "http://127.0.0.1:8004/api/v1"

DEFAULT_TEXT = "There has been no water supply in our street for the past four days"
# Inside the seeded Chennai ward, so the request resolves to a real ward.
DEFAULT_LATITUDE = 13.0827
DEFAULT_LONGITUDE = 80.2707

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def call(url: str, payload: Optional[dict[str, Any]] = None, timeout: float = 20.0) -> Any:
    """One HTTP call. Raises with the body included, so failures are diagnosable."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"{url} -> HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} is unreachable: {exc.reason}") from exc


def step(number: int, text: str) -> None:
    print(f"\n{YELLOW}[{number}/5]{RESET} {text}")


def ok(text: str) -> None:
    print(f"  {GREEN}PASS{RESET} {text}")


def info(text: str) -> None:
    print(f"  {DIM}{text}{RESET}")


def wait_for(description: str, check, timeout: float = 45.0, interval: float = 1.0):
    """Poll until ``check()`` returns something truthy, or give up honestly."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = check()
            if last:
                return last
        except RuntimeError as exc:
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {description} (last saw: {last})")


def seed_mesh() -> dict[str, Any]:
    """Load Module 4's reference layers (wards, assets, census).

    Only the reference data is seeded -- no citizen demand. The demand row that
    this test asserts on is produced by the real submission, not by the seeder.
    """
    import subprocess
    from pathlib import Path

    module4 = Path(__file__).resolve().parent.parent / "module-4-national-data-mesh"
    result = subprocess.run(
        ["python3", "seed_mesh.py"], cwd=module4, capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"seeding failed: {result.stderr.strip()[-500:]}")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--latitude", type=float, default=DEFAULT_LATITUDE)
    parser.add_argument("--longitude", type=float, default=DEFAULT_LONGITUDE)
    parser.add_argument("--keep", action="store_true", help="keep the seeded mesh")
    args = parser.parse_args()

    print("Civic pipeline end-to-end test: Module 1 -> 2 -> 3 -> 4")

    step(1, "Checking every module is healthy before we start")
    health = {}
    for name, base in (("module1", MODULE1), ("module2", MODULE2),
                       ("module3", MODULE3), ("module4", MODULE4)):
        body = call(f"{base}/health")
        health[name] = body
        status = body.get("status")
        if status not in ("ok", "healthy", "degraded"):
            raise AssertionError(f"{name} health is {status!r}: {body}")
        info(f"{name}: {status}")
    ok("all four modules are serving")

    step(2, "Seeding Module 4's reference layers (wards/assets/census, no demand)")
    seeded = seed_mesh()
    wards = call(f"{MODULE4}/wards")
    if not wards:
        raise AssertionError("Module 4 has no wards to attribute the submission to")
    info(f"{len(wards)} ward(s) available; seeder reported {seeded.get('wards', '?')}")

    step(3, "Submitting a citizen complaint to Module 1 over HTTP")
    created = call(
        f"{MODULE1}/requests",
        {
            "text": args.text,
            "latitude": args.latitude,
            "longitude": args.longitude,
            "channel": "web",
        },
    )
    request_id = created.get("request_id")
    if not request_id:
        raise AssertionError(f"Module 1 did not return a request_id: {created}")
    if not created.get("event_id"):
        raise AssertionError(f"Module 1 published no event_id: {created}")
    ok(f"Module 1 accepted {request_id} (event {created['event_id']})")
    info(f"pipeline_status={created.get('pipeline_status')}")

    step(4, "Following the request_id through Modules 2 and 3")
    understanding = wait_for(
        "Module 2 to understand the request",
        lambda: call(f"{MODULE2}/understand/{request_id}"),
    )
    category = understanding.get("category")
    severity = understanding.get("severity")
    info(f"Module 2: category={category} severity={severity}")
    if not category:
        raise AssertionError(f"Module 2 produced no category: {understanding}")
    ok("Module 2 understood the submission")

    civic = wait_for(
        "Module 3 to create a civic record",
        lambda: call(f"{MODULE3}/civic/{request_id}"),
    )
    issue_group_id = civic.get("issue_group_id")
    info(f"Module 3: issue_group_id={issue_group_id} quality={civic.get('data_quality_status')}")
    if not issue_group_id:
        raise AssertionError(f"Module 3 produced no issue group: {civic}")
    ok("Module 3 processed and de-duplicated the record")

    step(5, "Asserting Module 4 recorded it with full lineage")
    lineage = wait_for(
        "Module 4 to record lineage for the request",
        lambda: call(f"{MODULE4}/lineage/{request_id}"),
    )
    ok(f"Module 4 lineage: ward={lineage['ward_code']} sector={lineage['sector']} "
       f"category={lineage['category']} severity={lineage['severity']}")
    info(f"source_event_id={lineage['source_event_id']}")

    if not lineage["source_event_id"]:
        raise AssertionError("lineage lost the Module 3 event_id; provenance is broken")
    if lineage["category"] != category:
        raise AssertionError(
            f"category changed in transit: Module 2 said {category!r}, Module 4 stored "
            f"{lineage['category']!r}"
        )
    ok("the request kept its identity and event provenance across all three hops")

    query = f"ward_code={lineage['ward_code']}&sector={lineage['sector']}"
    demand_rows = call(f"{MODULE4}/demand?{query}")
    real = [row for row in demand_rows if row.get("source") != "synthetic"]
    if not real:
        raise AssertionError(
            f"no ingested demand for {lineage['ward_code']}/{lineage['sector']}; "
            f"saw sources {[row.get('source') for row in demand_rows]}. The submission "
            "reached lineage but not the aggregate the gap score is computed from."
        )
    aggregate = real[0]
    ok(f"citizen_demand aggregate updated: {aggregate['complaint_count']} complaint(s) "
       f"in {aggregate['ward_code']}/{aggregate['sector']} (source={aggregate['source']})")

    # And the aggregate must be traceable back to the individual requests.
    submissions = call(f"{MODULE4}/lineage?{query}")
    if not any(row["request_id"] == request_id for row in submissions):
        raise AssertionError(
            f"the aggregate for {lineage['ward_code']}/{lineage['sector']} does not list "
            f"{request_id}; the score would be unauditable"
        )
    ok(f"the aggregate lists the individual submissions behind it ({len(submissions)} shown)")

    print(f"\n{GREEN}End-to-end test passed.{RESET} "
          f"{request_id} travelled Module 1 -> 2 -> 3 -> 4 and is traceable at every hop.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, RuntimeError) as exc:
        print(f"\n{RED}E2E FAILED{RESET} {exc}")
        sys.exit(1)
