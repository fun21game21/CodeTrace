"""Opt-in ~50 MiB recorder proof in a fresh process; not an PERFORMANCE benchmark."""

import asyncio
import hashlib
import json
import resource
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from codetrace_backend.runs import LifecycleEvent, SystemClock, TerminalResult
from codetrace_backend.trace_recorder import ResultService, TraceRecorder
from codetrace_contracts import default_limits


def rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def test_near_50_mib_streaming_memory_and_get():
    async def run():
        clock = SystemClock()
        limits = default_limits()
        run_id = str(uuid4())
        recorder = TraceRecorder(run_id, limits)
        baseline = rss_bytes()
        service = ResultService(clock=clock)
        code = "😀" * 16384  # Exact maximum 64 KiB source; ASCII escaping is counted.
        service.register(recorder, code)
        accepted = clock.utc()
        service.observe(LifecycleEvent("accepted", run_id, {"acceptedAt": accepted}))
        empty = {
            "version": 0,
            "globals": [],
            "frames": [],
            "stack": [],
            "objects": [],
            "eventRoots": [],
        }
        await recorder.commit_chunk(
            [{"kind": "state", "record": {"kind": "checkpoint", "version": 0, "state": empty}}]
        )
        count = 3050
        for version in range(1, count + 1):
            bindings = [
                {
                    "name": "x",
                    "value": {
                        "kind": "scalar",
                        "type": "str",
                        "value": str(version).zfill(5) + "a" * 15995,
                    },
                }
            ]
            if version % 128 == 0:
                state = {
                    "kind": "checkpoint",
                    "version": version,
                    "state": {**empty, "version": version, "globals": bindings},
                }
            else:
                state = {
                    "kind": "delta",
                    "version": version,
                    "baseVersion": version - 1,
                    "changes": {
                        "globals": bindings,
                        "stack": None,
                        "eventRoots": None,
                        "framesUpsert": [],
                        "framesRemove": [],
                        "objectsUpsert": [],
                        "objectsRemove": [],
                    },
                }
            event = {
                "index": version - 1,
                "kind": "LINE",
                "stateVersion": version,
                "frameId": "f1",
                "source": {"fileName": "<codetrace-user>", "lineNumber": 1},
                "consoleOffset": 0,
                "payload": {},
            }
            await recorder.commit_chunk(
                [{"kind": "state", "record": state}, {"kind": "event", "record": event}]
            )
        bindings = [
            {"name": f"x{i:02}", "value": {"kind": "scalar", "type": "str", "value": "b" * 16000}}
            for i in range(64)
        ]
        final_version = count + 1
        final = {
            "kind": "delta",
            "version": final_version,
            "baseVersion": count,
            "changes": {
                "globals": bindings,
                "stack": None,
                "eventRoots": None,
                "framesUpsert": [],
                "framesRemove": [],
                "objectsUpsert": [],
                "objectsRemove": [],
            },
        }
        await recorder.commit_chunk([{"kind": "state", "record": final}])
        execution = TerminalResult(
            run_id,
            "SUCCEEDED",
            "SUCCESS",
            "NONE",
            None,
            None,
            final_version,
            0,
            False,
            True,
            1,
            0,
            accepted,
            clock.utc(),
            True,
            count,
            0,
        )

        class Lease:
            calls = 0
            session = SimpleNamespace(runId=run_id, limits=limits, acceptedAt=accepted, _code=code)

            async def result(self):
                return execution

            def finish_ownership(self):
                self.calls += 1

        lease = Lease()
        lease.session.await_result = lease.result
        service.observe(LifecycleEvent("status", run_id, "FINALIZING"))
        handle = service.bind(lease)
        try:
            summary = await handle.await_result()
            assert summary.traceAvailable, summary.completion["reason"]
            assert 48 * 1024**2 <= summary.traceBytes <= limits["trace_bytes"]
            digests = []
            for _ in range(2):
                digest = hashlib.sha256()
                total = 0
                with service.get_trace(run_id) as access:
                    while chunk := access.read():
                        total += len(chunk)
                        digest.update(chunk)
                assert total == summary.traceBytes
                digests.append(digest.hexdigest())
            assert digests[0] == digests[1]
            peak = rss_bytes()
            growth = max(0, peak - baseline)
            assert growth < 24 * 1024**2  # Well below one whole 50 MiB document.
            assert not any(isinstance(v, list) for v in vars(recorder).values())
            report = {
                "traceBytes": summary.traceBytes,
                "events": summary.eventCount,
                "states": recorder.counters["stateCount"],
                "baselineRssBytes": baseline,
                "peakRssBytes": peak,
                "rssGrowthBytes": growth,
                "diskBytes": sum(p.stat().st_size for p in recorder.directory.iterdir()),
                "sha256": digests[0],
                "getPasses": 2,
                "parsedWholeTrace": False,
            }
            Path(tempfile.gettempdir(), "codetrace-recorder-scale.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            print(json.dumps(report))
        finally:
            await service.shutdown()
            assert not recorder.directory.exists() and lease.calls == 1

    asyncio.run(run())
