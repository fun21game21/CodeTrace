"""Explicit real Engine suite. No skip/fake fallback and no production orchestration."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from codetrace_backend.docker_adapter import (
    ContainerHandle,
    DockerAdapter,
    DockerOperationError,
    ImageIdentity,
    ImageUnavailableError,
)
from codetrace_contracts import default_limits, validate_contract

ROOT = Path(__file__).resolve().parents[2]
OWNED_IDS = set()
CONFIG_IDS = set()
TEST_SESSION = os.environ.get("CODETRACE_DOCKER_TEST_SESSION", str(uuid4()))


class TestAdapter(DockerAdapter):
    __test__ = False

    def create_argv(self, run_id, token):
        argv = list(super().create_argv(run_id, token))
        argv[-1:-1] = ["--label", "codetrace.docker_test=" + TEST_SESSION]
        return tuple(argv)


def adapter():
    return TestAdapter(ImageIdentity.load(ROOT / "backend/local-image.json"))


class Driver:
    def __init__(self, attachment, run_id):
        self.attachment = attachment
        self.run_id = run_id
        self.seq = 0
        self.buffer = bytearray()
        self.messages = []

    async def message(self):
        while b"\n" not in self.buffer:
            chunk = await asyncio.wait_for(self.attachment.stdout.read(4096), 10)
            assert chunk, "Unexpected transport EOF"
            self.buffer.extend(chunk)
            assert len(self.buffer) <= 2097152 + 4096
        raw, _, remaining = self.buffer.partition(b"\n")
        self.buffer[:] = remaining
        message = json.loads(raw)
        validate_contract(message, "runner")
        assert message["runId"] == self.run_id
        assert message["seq"] == len(self.messages) + 1
        if message["type"] == "trace_chunk":
            for item in message["payload"]["records"]:
                validate_contract(
                    item["record"],
                    "trace",
                    "StateRecord" if item["kind"] == "state" else "PythonTraceEvent",
                )
        self.messages.append(message)
        return message

    async def send(self, kind, payload):
        self.seq += 1
        envelope = {
            "protocolVersion": 1,
            "runId": self.run_id,
            "seq": self.seq,
            "type": kind,
            "payload": payload,
        }
        validate_contract(envelope, "runner")
        self.attachment.stdin.write(
            json.dumps(envelope, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        )
        await self.attachment.stdin.drain()

    async def start(self, source):
        message = await self.message()
        assert message["type"] == "ready" and message["payload"]["pythonVersion"] == "3.13.16"
        limits = default_limits()
        await self.send(
            "start",
            {
                "code": source,
                "sourceFileName": "<codetrace-user>",
                "limits": limits,
                "checkpointInterval": limits["checkpoint_interval"],
            },
        )


async def native_drain(attachment):
    count = 0
    while chunk := await attachment.stderr.read(4096):
        count += len(chunk)
        assert count <= 16384
    return count


async def run_source(source, responses=(), *, malformed=False):
    api = adapter()
    handle = await api.create(str(uuid4()))
    OWNED_IDS.add(handle.id)
    attachment = None
    native = None
    try:
        attachment = await api.start_attach(handle)
        native = asyncio.create_task(native_drain(attachment))
        driver = Driver(attachment, handle.runId)
        if malformed:
            assert (await driver.message())["type"] == "ready"
            attachment.stdin.write(b"not JSON\n")
            await attachment.stdin.drain()
        else:
            await driver.start(source)
        while True:
            message = await driver.message()
            if message["type"] == "input_request":
                n = int(message["payload"]["requestId"][1:])
                before = await api.inspect(handle)
                await driver.send(
                    "input_response", {"requestId": f"q{n}", "text": responses[n - 1]}
                )
                after = await api.inspect(handle)
                assert before.id == after.id and before.startedAt == after.startedAt
            if message["type"] in ("finished", "fatal", "abort_request"):
                break
        assert await asyncio.wait_for(attachment.stdout.read(), 5) == b""
        assert await native == 0
        code = await api.wait(handle)
        assert await attachment.wait_cli(3000) == code
        inspection = await api.inspect(handle)
        assert not inspection.running
        if message["type"] == "finished":
            assert inspection.exitCode == 0 and code == 0
        result = await api.cleanup(handle)
        assert result.cleanupConfirmed
        assert await api.inspect(handle) is None
        return driver.messages
    finally:
        result = await api.cleanup(handle)
        assert result.cleanupConfirmed
        if native is not None:
            native.cancel()
            await asyncio.gather(native, return_exceptions=True)
        if attachment is not None:
            await attachment.close()


@pytest.fixture(scope="module")
def created_container():
    api = adapter()
    handle = asyncio.run(api.create(str(uuid4())))
    CONFIG_IDS.add(handle.id)
    raw = asyncio.run(api._command(("container", "inspect", handle.id)))
    yield json.loads(raw)[0]
    assert asyncio.run(api.cleanup(handle)).cleanupConfirmed
    assert asyncio.run(api.inspect(handle)) is None


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("HostConfig", "NetworkMode", "none"),
        ("Config", "User", "65532:65532"),
        ("HostConfig", "ReadonlyRootfs", True),
        ("HostConfig", "Privileged", False),
        ("HostConfig", "Init", True),
        ("Config", "Tty", False),
        ("Config", "OpenStdin", True),
        ("Config", "AttachStdin", True),
        ("Config", "AttachStdout", True),
        ("Config", "AttachStderr", True),
        ("HostConfig", "Memory", 536870912),
        ("HostConfig", "MemorySwap", 536870912),
        ("HostConfig", "NanoCpus", 1000000000),
        ("HostConfig", "PidsLimit", 16),
    ],
)
def test_actual_config(created_container, section, key, value):
    assert created_container[section][key] == value


def test_actual_labels_image_entrypoint(created_container):
    item = created_container
    assert item["Config"]["Labels"]["codetrace.managed"] == "true"
    assert item["Name"] == "/codetrace-" + item["Config"]["Labels"]["codetrace.run_id"]
    assert (
        item["Config"]["Env"].count(
            "CODETRACE_RUN_ID=" + item["Config"]["Labels"]["codetrace.run_id"]
        )
        == 1
    )
    assert item["Image"] == adapter().image.imageId
    assert item["Config"]["Entrypoint"] == ["python", "-m", "codetrace_runner.runner"]


def test_actual_tmpfs_privileges_namespaces_logging(created_container):
    host = created_container["HostConfig"]
    tmpfs = host["Tmpfs"]["/tmp"]
    assert {"rw", "noexec", "nosuid", "size=16777216", "mode=1777"} <= set(tmpfs.split(","))
    assert host["CapDrop"] == ["ALL"] and not host["CapAdd"]
    assert any(value.startswith("no-new-privileges") for value in host["SecurityOpt"])
    assert host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
    assert host["PidMode"] != "host" and host["IpcMode"] != "host"
    assert not host["Binds"] and not host["Devices"] and not host.get("DeviceRequests")


@pytest.mark.parametrize(
    "source,responses,reason",
    [
        ("x=1", [], "NONE"),
        ("print('A')", [], "NONE"),
        ("from collections import _sys\nprint('E',file=_sys.stderr)", [], "NONE"),
        ("name=input('Name: ')\nprint(name)", ["Name"], "NONE"),
        ("a=input('A:')\nb=input('B:')\nprint(a,b)", ["", " spaces 😀 "], "NONE"),
        ("x=", [], "SYNTAX_ERROR"),
        ("x=1\n1/0", [], "UNCAUGHT_EXCEPTION"),
        ("import os", [], "UNSUPPORTED_FEATURE"),
        ('print(\'{"fake":"protocol"}\')', [], "NONE"),
    ],
)
def test_actual_runner(source, responses, reason):
    messages = asyncio.run(run_source(source, responses))
    assert messages[-1]["type"] == "finished" and messages[-1]["payload"]["reason"] == reason
    entries = [m["payload"]["entry"] for m in messages if m["type"] == "output"]
    if "_sys.stderr" in source:
        assert any(e["kind"] == "stderr" for e in entries)
    if "input(" in source:
        assert [m["payload"]["requestId"] for m in messages if m["type"] == "input_request"] == [
            f"q{i + 1}" for i in range(len(responses))
        ]
        assert [e["text"] for e in entries if e["kind"] == "input"] == [
            text + "\n" for text in responses
        ]


def test_malformed_interaction_cleanup():
    messages = asyncio.run(run_source("", malformed=True))
    assert messages[-1]["type"] == "fatal"


def test_hard_kill_wait_inspect_cleanup():
    async def scenario():
        api = adapter()
        handle = await api.create(str(uuid4()))
        OWNED_IDS.add(handle.id)
        attachment = None
        native = None
        try:
            attachment = await api.start_attach(handle)
            native = asyncio.create_task(native_drain(attachment))
            driver = Driver(attachment, handle.runId)
            await driver.start("input('Wait: ')")
            while (await driver.message())["type"] != "input_request":
                pass
            assert (await api.inspect(handle)).running
            assert await api.kill(handle)
            await asyncio.wait_for(attachment.stdout.read(), 5)
            assert await native == 0
            assert await api.wait(handle) == 137
            await attachment.wait_cli(3000)
            inspection = await api.inspect(handle)
            assert not inspection.running and inspection.exitCode == 137
            assert not await api.kill(handle)
        finally:
            assert (await api.cleanup(handle)).cleanupConfirmed
            if native:
                native.cancel()
                await asyncio.gather(native, return_exceptions=True)
            if attachment:
                await attachment.close()
        assert await api.inspect(handle) is None

    asyncio.run(scenario())


def test_real_image_identity_missing_and_mismatch():
    async def scenario():
        api = adapter()
        assert await api.verify_image() == api.image
        wrong = DockerAdapter(ImageIdentity("sha256:" + "0" * 64))
        with pytest.raises(ImageUnavailableError):
            await wrong.verify_image()
        # Missing immutable image: no create/pull, explicit preparation only.
        result = await api._executor.run(("docker", "image", "inspect", "sha256:" + "0" * 64), 3000)
        assert result.returncode != 0

    asyncio.run(scenario())


def test_image_runtime_nonroot_readonly_tmpfs():
    async def scenario():
        api = adapter()
        run_id = str(uuid4())
        argv = list(api.create_argv(run_id, str(uuid4())))
        argv[-1:-1] = ["--entrypoint=python"]
        probe = """import json,os,sys,sysconfig
from pathlib import Path
from codetrace_runner.runner import runtime_supported
assert runtime_supported() and os.getuid()==65532
try:
 Path('/codetrace-root-write').write_text('x')
except OSError:
 pass
else:
 raise AssertionError('root writable')
Path('/tmp/codetrace-smoke').write_text('ok')
assert Path('/tmp/codetrace-smoke').read_text()=='ok'
print(json.dumps({'version':list(sys.version_info),'uid':os.getuid(),'gil':sys._is_gil_enabled(),'freeThreaded':bool(sysconfig.get_config_var('Py_GIL_DISABLED')),'jit':bool(sysconfig.get_config_var('_PY_JIT'))}))
"""
        argv += ["-c", probe]
        raw = await api._command(tuple(argv[1:]), 15000)
        handle = ContainerHandle(raw.decode().strip(), run_id, "codetrace-" + run_id)
        OWNED_IDS.add(handle.id)
        attachment = None
        try:
            attachment = await api.start_attach(handle)
            out_task = asyncio.create_task(attachment.stdout.read())
            err_task = asyncio.create_task(attachment.stderr.read())
            stdout, stderr = await asyncio.wait_for(asyncio.gather(out_task, err_task), 10)
            assert stderr == b"" and await api.wait(handle) == 0
            dto = json.loads(stdout)
            assert dto["version"] == [3, 13, 16, "final", 0] and dto["uid"] == 65532
            assert dto["gil"] and not dto["freeThreaded"] and not dto["jit"]
        finally:
            assert (await api.cleanup(handle)).cleanupConfirmed
            if attachment:
                await attachment.close()

    asyncio.run(scenario())


def test_real_start_failure_leaves_no_container():
    async def scenario():
        api = adapter()
        run_id = str(uuid4())
        argv = list(api.create_argv(run_id, str(uuid4())))
        # Fault injection only: with production --init, init starts successfully
        # and exits 127. Disable it here to trigger an actual OCI start failure.
        argv[-1:-1] = ["--init=false", "--entrypoint=/codetrace-does-not-exist"]
        raw = await api._command(tuple(argv[1:]), 15000)
        handle = ContainerHandle(raw.decode().strip(), run_id, "codetrace-" + run_id)
        OWNED_IDS.add(handle.id)
        try:
            with pytest.raises(DockerOperationError):
                await api.start_attach(handle)
        finally:
            assert (await api.cleanup(handle)).cleanupConfirmed
            assert await api.inspect(handle) is None

    asyncio.run(scenario())


def test_real_immediate_nonzero_exit_is_not_start_failure():
    async def scenario():
        api = adapter()
        run_id = str(uuid4())
        argv = list(api.create_argv(run_id, str(uuid4())))
        argv[-1:-1] = ["--entrypoint=/codetrace-does-not-exist"]
        raw = await api._command(tuple(argv[1:]), 15000)
        handle = ContainerHandle(raw.decode().strip(), run_id, "codetrace-" + run_id)
        OWNED_IDS.add(handle.id)
        attachment = None
        readers = []
        try:
            attachment = await api.start_attach(handle)
            readers = [
                asyncio.create_task(attachment.stdout.read()),
                asyncio.create_task(attachment.stderr.read()),
            ]
            async with asyncio.timeout(10):
                stdout, stderr = await asyncio.gather(*readers)
            assert stdout == b"" and stderr
            assert await attachment.wait_cli(3000) == 127
            assert await api.wait(handle) == 127
            inspection = await api.inspect(handle)
            assert inspection.status == "exited" and not inspection.running
            assert inspection.startedAt is not None and inspection.exitCode == 127
        finally:
            assert (await api.cleanup(handle)).cleanupConfirmed
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if attachment:
                await attachment.close()
            assert await api.inspect(handle) is None

    asyncio.run(scenario())


def test_foreign_real_container_not_selected_or_removed():
    async def scenario():
        api = adapter()
        run_id = str(uuid4())
        argv = list(api.create_argv(run_id, str(uuid4())))
        index = argv.index("codetrace.managed=true")
        argv[index] = "codetrace.managed=false"
        raw = await api._command(tuple(argv[1:]), 15000)
        cid = raw.decode().strip()
        try:
            assert cid not in {h.id for h in await api.list_managed_containers()}
            handle = ContainerHandle(cid, run_id, "codetrace-" + run_id)
            assert not (await api.cleanup(handle)).cleanupConfirmed
            assert await api._lookup(cid) == cid
        finally:
            # Test created this exact foreign fixture; do not touch pre-existing ones.
            await api._command(("rm", cid))
            assert await api._lookup(cid) is None

    asyncio.run(scenario())


def test_z_no_remaining_test_containers():
    async def scenario():
        api = adapter()
        for identifier in OWNED_IDS:
            assert await api._lookup(identifier) is None
        for identifier in CONFIG_IDS:
            assert (await api._inspect_id(identifier)).status == "created"

    asyncio.run(scenario())


def test_real_unknown_create_outcome_recovery():
    from codetrace_backend.docker_adapter import CreateOutcomeError, DockerCommandTimeout
    from codetrace_backend.docker_adapter.cli import DockerCLI

    class TimeoutAfterCreate(DockerCLI):
        async def run(self, argv, timeout_ms):
            result = await super().run(argv, timeout_ms)
            if argv[1] == "create" and result.returncode == 0:
                OWNED_IDS.add(result.stdout.decode().strip())
                raise DockerCommandTimeout("injected after actual create")
            return result

    async def scenario():
        api = TestAdapter(
            ImageIdentity.load(ROOT / "backend/local-image.json"), executor=TimeoutAfterCreate()
        )
        run_id = str(uuid4())
        with pytest.raises(CreateOutcomeError) as caught:
            await api.create(run_id)
        assert caught.value.cleanupConfirmed
        assert await api._lookup("codetrace-" + run_id, by_name=True) is None

    asyncio.run(scenario())
