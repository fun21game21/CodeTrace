import asyncio
import copy
import json

from codetrace_backend.docker_adapter import ImageIdentity
from codetrace_backend.docker_adapter.cli import CommandResult
from codetrace_backend.docker_adapter.errors import DockerCommandTimeout, DockerOperationError

RUN_ID = "12345678-1234-4234-8234-123456789abc"
TOKEN = "22345678-1234-4234-8234-123456789abc"
CID = "a" * 64
IMAGE = "sha256:" + "b" * 64


def inspection(cid=CID, run_id=RUN_ID, *, managed=True, running=False, status="created"):
    return {
        "Id": cid,
        "Name": "/codetrace-" + run_id,
        "Image": IMAGE,
        "Config": {
            "Labels": {
                "codetrace.managed": "true" if managed else "false",
                "codetrace.run_id": run_id,
            }
        },
        "State": {
            "Status": status,
            "Running": running,
            "ExitCode": 0,
            "OOMKilled": False,
            "StartedAt": "0001-01-01T00:00:00Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
        },
    }


class FakeAttached:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.closed = False

    async def close(self):
        self.closed = True


class FakeCLI:
    def __init__(self):
        self.containers = {}
        self.calls = []
        self.available = True
        self.image = IMAGE
        self.image_missing = False
        self.create_mode = "normal"
        self.rm_failures = 0
        self.inspect_failures = 0
        self.attach_mode = "normal"
        self.attachment = None
        self.wait_value = b"0\n"
        self.list_override = None
        self.kill_fail = False
        self.rm_ignores = False

    async def run(self, argv, timeout_ms):
        self.calls.append((argv, timeout_ms))
        op = argv[1]

        def ok(value=b""):
            return CommandResult(0, value, b"")

        def fail():
            return CommandResult(1, b"", b"RAW SECRET ERROR")

        if not self.available:
            return fail()
        if op == "info":
            return ok(json.dumps("29.7.2").encode())
        if op == "image":
            return fail() if self.image_missing else ok(json.dumps(self.image).encode())
        if op == "container" and argv[2] == "ls":
            if self.list_override is not None:
                return ok(self.list_override)
            criterion = argv[argv.index("--filter") + 1]
            items = []
            for cid, item in self.containers.items():
                if criterion.startswith("id=") and cid == criterion[3:]:
                    items.append(cid)
                if criterion.startswith("name=") and item["Name"] == criterion[6:-1]:
                    items.append(cid)
                if (
                    criterion == "label=codetrace.managed=true"
                    and item["Config"]["Labels"].get("codetrace.managed") == "true"
                ):
                    items.append(cid)
            return ok(("\n".join(items)).encode())
        if op == "container" and argv[2] == "inspect":
            if self.inspect_failures:
                self.inspect_failures -= 1
                return fail()
            return (
                ok(json.dumps([self.containers[argv[-1]]]).encode())
                if argv[-1] in self.containers
                else fail()
            )
        if op == "create":
            if self.create_mode == "failure":
                return fail()
            item = inspection()
            item["Name"] = "/" + argv[argv.index("--name") + 1]
            labels = {
                value.split("=", 1)[0]: value.split("=", 1)[1]
                for i, value in enumerate(argv)
                if i and argv[i - 1] == "--label"
            }
            item["Config"]["Labels"] = labels
            self.containers[CID] = item
            if self.create_mode == "cancel":
                raise asyncio.CancelledError()
            if self.create_mode == "timeout":
                raise DockerCommandTimeout("create")
            if self.create_mode == "disconnect":
                self.available = False
                return fail()
            if self.create_mode == "foreign-race":
                item["Config"]["Labels"] = {"codetrace.managed": "false"}
                return fail()
            if self.create_mode == "other-token":
                item["Config"]["Labels"]["codetrace.create_token"] = "other"
                return fail()
            if self.create_mode == "empty":
                return ok(b"")
            if self.create_mode == "malformed":
                return ok(b"invalid ID\n")
            return ok(CID.encode() + b"\n")
        if op == "wait":
            return ok(self.wait_value)
        if op == "kill":
            if self.kill_fail:
                return fail()
            self.containers[argv[-1]]["State"].update(Running=False, Status="exited", ExitCode=137)
            return ok(argv[-1].encode())
        if op == "rm":
            if self.rm_failures:
                self.rm_failures -= 1
                return fail()
            if not self.rm_ignores:
                self.containers.pop(argv[-1], None)
            return ok(argv[-1].encode())
        raise AssertionError(argv)

    async def attach(self, argv, timeout_ms):
        self.calls.append((argv, timeout_ms))
        if self.attach_mode == "spawn-fail":
            raise DockerOperationError("attach")
        self.attachment = FakeAttached(1 if self.attach_mode == "failure" else None)
        if self.attach_mode == "normal":
            self.containers[argv[-1]]["State"].update(Running=True, Status="running")
        if self.attach_mode in ("immediate-exit", "immediate-error"):
            code = 127 if self.attach_mode == "immediate-error" else 0
            self.containers[argv[-1]]["State"].update(Running=False, Status="exited", ExitCode=code)
            self.attachment.returncode = code
        return self.attachment


def run(coro):
    return asyncio.run(coro)


def clone(item):
    return copy.deepcopy(item)


def image_identity():
    return ImageIdentity(IMAGE)
