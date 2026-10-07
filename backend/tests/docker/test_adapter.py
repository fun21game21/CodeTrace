import json

import pytest
from codetrace_backend.docker_adapter import (
    ContainerConflictError,
    ContainerHandle,
    CreateOutcomeError,
    DockerAdapter,
    DockerOperationError,
    DockerOutputError,
    DockerUnavailableError,
    ForeignContainerError,
    ImageIdentity,
    ImageUnavailableError,
)
from codetrace_backend.docker_adapter.adapter import parse_inspection
from codetrace_contracts import default_limits

from .helpers import CID, IMAGE, RUN_ID, TOKEN, FakeCLI, image_identity, inspection, run


@pytest.fixture
def pair():
    cli = FakeCLI()
    return DockerAdapter(image_identity(), executor=cli), cli


@pytest.mark.parametrize(
    "flag",
    [
        "--pull=never",
        "--network=none",
        "--user=65532:65532",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--init",
        "--restart=no",
        "--log-driver=none",
        "--interactive",
        "--attach=stdin",
        "--attach=stdout",
        "--attach=stderr",
    ],
)
def test_security_flags(pair, flag):
    adapter, _ = pair
    assert flag in adapter.create_argv(RUN_ID, TOKEN)


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--name", "codetrace-" + RUN_ID),
        ("--env", "CODETRACE_RUN_ID=" + RUN_ID),
        ("--tmpfs", "/tmp:rw,noexec,nosuid,size=16777216,mode=1777"),
        ("--memory", "536870912"),
        ("--memory-swap", "536870912"),
        ("--cpus", "1.0"),
        ("--pids-limit", "16"),
    ],
)
def test_resource_flags(pair, flag, value):
    argv = pair[0].create_argv(RUN_ID, TOKEN)
    assert argv[argv.index(flag) + 1] == value


def test_labels_image_and_no_source_or_dangerous_flags(pair):
    argv = pair[0].create_argv(RUN_ID, TOKEN)
    assert isinstance(argv, tuple) and argv[:2] == ("docker", "create")
    assert "codetrace.managed=true" in argv and "codetrace.run_id=" + RUN_ID in argv
    assert "codetrace.create_token=" + TOKEN in argv
    assert argv[-1] == IMAGE
    assert pair[0].image.imageRef == "codetrace-runner:1.0"
    assert sum(arg == "--env" for arg in argv) == 1
    assert all(
        flag not in argv
        for flag in (
            "--tty",
            "-t",
            "--privileged",
            "--mount",
            "--volume",
            "--pid=host",
            "--ipc=host",
            "--device",
            "--cap-add",
        )
    )
    assert "source" not in " ".join(argv) and "input" not in " ".join(argv)


@pytest.mark.parametrize(
    "value", ["", "invalid", "12345678123442348234123456789abc", RUN_ID + ";touch x"]
)
def test_bad_run_id_never_invokes_docker(pair, value):
    with pytest.raises(ValueError):
        run(pair[0].create(value))
    assert pair[1].calls == []


def test_limits_copied(pair):
    limits = default_limits()
    limits["memory_bytes"] = 300000000
    limits["memory_plus_swap_bytes"] = 300000000
    adapter = DockerAdapter(image_identity(), limits, executor=pair[1])
    limits["memory_bytes"] = 1
    argv = adapter.create_argv(RUN_ID, TOKEN)
    assert argv[argv.index("--memory") + 1] == "300000000"


def test_verify_image(pair):
    assert run(pair[0].verify_image()).imageId == IMAGE
    assert pair[1].calls[0][0][-1] == "codetrace-runner:1.0"


@pytest.mark.parametrize("mode", ["missing", "mismatch", "daemon"])
def test_image_errors(pair, mode):
    adapter, cli = pair
    cli.image_missing = mode == "missing"
    if mode == "mismatch":
        cli.image = "sha256:" + "c" * 64
    if mode == "daemon":
        cli.available = False
    error = DockerUnavailableError if mode == "daemon" else ImageUnavailableError
    with pytest.raises(error):
        run(adapter.verify_image())
    assert not any("pull" in argv for argv, _ in cli.calls)


def test_image_identity_load(tmp_path):
    path = tmp_path / "image.json"
    path.write_text(json.dumps({"imageId": IMAGE, "imageRef": "codetrace-runner:1.0"}))
    assert ImageIdentity.load(path) == image_identity()


@pytest.mark.parametrize("bad", ["sha256:bad", "other", "sha256:" + "g" * 64])
def test_invalid_image_identity(bad):
    with pytest.raises(ValueError):
        ImageIdentity(bad)


def test_create_and_operations_use_id(pair):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    assert handle == ContainerHandle(CID, RUN_ID, "codetrace-" + RUN_ID)
    assert run(adapter.inspect(handle)).id == CID
    assert run(adapter.wait(handle)) == 0
    assert run(adapter.kill(handle)) is False
    run(adapter.remove(handle))
    assert cli.containers == {}
    assert all(
        argv[-1] == CID for argv, _ in cli.calls if argv[1] == "container" and argv[2] == "inspect"
    )
    assert all(argv[-1] == CID for argv, _ in cli.calls if argv[1] in ("wait", "rm", "kill"))


@pytest.mark.parametrize("mode", ["timeout", "malformed", "empty", "failure"])
def test_unknown_create_recovery(pair, mode):
    adapter, cli = pair
    cli.create_mode = mode
    with pytest.raises(CreateOutcomeError) as caught:
        run(adapter.create(RUN_ID))
    assert caught.value.cleanupConfirmed
    assert cli.containers == {}
    assert any("name=^/codetrace-" + RUN_ID + "$" in argv for argv, _ in cli.calls)


@pytest.mark.parametrize("mode", ["foreign-race", "other-token", "disconnect"])
def test_unknown_create_never_false_confirmation(pair, mode):
    adapter, cli = pair
    cli.create_mode = mode
    with pytest.raises(CreateOutcomeError) as caught:
        run(adapter.create(RUN_ID))
    assert not caught.value.cleanupConfirmed and cli.containers
    assert not any(argv[1] == "rm" for argv, _ in cli.calls)
    if mode == "disconnect":
        assert caught.value.causeReason == "DOCKER_UNAVAILABLE"


@pytest.mark.parametrize("managed", [True, False])
def test_name_collision(pair, managed):
    adapter, cli = pair
    cli.containers[CID] = inspection(managed=managed)
    with pytest.raises(ContainerConflictError) as caught:
        run(adapter.create(RUN_ID))
    assert caught.value.managed is managed
    assert not any(argv[1] in ("create", "rm", "kill") for argv, _ in cli.calls)


@pytest.mark.parametrize("mode", ["normal", "immediate-exit", "immediate-error"])
def test_start_attach_and_separate_lifetime(pair, mode):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    cli.attach_mode = mode
    attachment = run(adapter.start_attach(handle))
    assert attachment is cli.attachment and not attachment.closed
    assert ("docker", "start", "--attach", "--interactive", CID) in [a for a, _ in cli.calls]


@pytest.mark.parametrize("mode", ["failure", "spawn-fail", "hang"])
def test_start_attach_failure_cleanup_possible(pair, mode):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    cli.attach_mode = mode
    adapter.limits["startup_timeout_ms"] = 10
    with pytest.raises(DockerOperationError):
        run(adapter.start_attach(handle))
    if cli.attachment:
        assert cli.attachment.closed
    assert run(adapter.cleanup(handle)).cleanupConfirmed
    assert not cli.containers


@pytest.mark.parametrize("text", [b"137\n", b"0\n", b"42"])
def test_wait_status(pair, text):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    cli.wait_value = text
    assert run(adapter.wait(handle)) == int(text)


@pytest.mark.parametrize("text", [b"", b"bad", b"256", b"-1", b"0\n1"])
def test_wait_bad_output(pair, text):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    cli.wait_value = text
    with pytest.raises(DockerOutputError):
        run(adapter.wait(handle))


def test_inspect_oom_and_dates():
    item = inspection(running=False, status="exited")
    item["State"].update(
        ExitCode=137,
        OOMKilled=True,
        StartedAt="2026-10-05T00:00:00Z",
        FinishedAt="2026-10-05T00:00:01Z",
    )
    dto = parse_inspection(json.dumps([item]).encode())
    assert dto.oomKilled and dto.exitCode == 137 and dto.startedAt and dto.finishedAt
    assert not dto.running and dto.status == "exited"
    with pytest.raises(TypeError):
        dto.labels["x"] = "y"


@pytest.mark.parametrize(
    "change",
    [("Running", "false"), ("OOMKilled", 0), ("ExitCode", True), ("Status", 3), ("StartedAt", 1)],
)
def test_inspect_bad_fields(change):
    item = inspection()
    item["State"][change[0]] = change[1]
    with pytest.raises(DockerOutputError):
        parse_inspection(json.dumps([item]).encode())


@pytest.mark.parametrize("raw", [b"", b"{}", b"[]", b"[{},{}]", b"invalid", b"\xff"])
def test_inspect_bad_json(raw):
    with pytest.raises(DockerOutputError):
        parse_inspection(raw)


def test_transient_inspect_failure_is_not_absence(pair):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    cli.inspect_failures = 1
    with pytest.raises(DockerOperationError):
        run(adapter.inspect(handle))
    assert cli.containers


def test_kill_then_cleanup(pair):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    run(adapter.start_attach(handle))
    assert run(adapter.kill(handle)) is True
    assert run(adapter.kill(handle)) is False
    assert run(adapter.inspect(handle)).exitCode == 137
    assert run(adapter.cleanup(handle)).cleanupConfirmed


@pytest.mark.parametrize("failures,success", [(0, True), (1, True), (2, False), (5, False)])
def test_cleanup_retry_and_failure(pair, failures, success):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    cli.rm_failures = failures
    result = run(adapter.cleanup(handle))
    assert result.cleanupConfirmed is success
    assert result.attempts == (1 if failures == 0 else 2)
    assert (result.error is None) is success
    if not success:
        assert result.error.reason == "CLEANUP_FAILED" and cli.containers


@pytest.mark.parametrize("mode", ["daemon", "ignored-remove", "kill-fails"])
def test_cleanup_never_confirms_failure(pair, mode):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    if mode == "daemon":
        cli.available = False
    if mode == "ignored-remove":
        cli.rm_ignores = True
    if mode == "kill-fails":
        run(adapter.start_attach(handle))
        cli.kill_fail = True
    assert not run(adapter.cleanup(handle)).cleanupConfirmed
    assert cli.containers


def test_already_removed_idempotent(pair):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    run(adapter.remove(handle))
    run(adapter.remove(handle))
    assert run(adapter.cleanup(handle)).cleanupConfirmed


@pytest.mark.parametrize("variant", ["no-label", "invalid-id", "wrong-name", "foreign"])
def test_foreign_enumeration_ignored(pair, variant):
    adapter, cli = pair
    item = inspection()
    if variant == "no-label":
        item["Config"]["Labels"] = {}
    if variant == "invalid-id":
        item["Config"]["Labels"]["codetrace.run_id"] = "not-uuid"
    if variant == "wrong-name":
        item["Name"] = "/similar-name"
    if variant == "foreign":
        item["Config"]["Labels"]["codetrace.managed"] = "false"
    cli.containers[CID] = item
    assert run(adapter.list_managed_containers()) == ()
    assert not any(argv[1] in ("kill", "rm") for argv, _ in cli.calls)


def test_managed_enumeration_explicit_cleanup(pair):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    assert run(adapter.list_managed_containers()) == (handle,)
    assert run(adapter.inspect_managed_container(handle)).id == CID
    assert run(adapter.cleanup_managed_container(handle)).cleanupConfirmed


def test_foreign_handle_never_mutated(pair):
    adapter, cli = pair
    cli.containers[CID] = inspection(managed=False)
    handle = ContainerHandle(CID, RUN_ID, "codetrace-" + RUN_ID)
    for method in (adapter.inspect, adapter.kill, adapter.remove):
        with pytest.raises(ForeignContainerError):
            run(method(handle))
    assert not run(adapter.cleanup(handle)).cleanupConfirmed
    assert not any(argv[1] in ("rm", "kill") for argv, _ in cli.calls)


def test_replacement_name_prevents_false_confirmation(pair):
    adapter, cli = pair
    handle = run(adapter.create(RUN_ID))
    run(adapter.remove(handle))
    cli.containers["c" * 64] = inspection(cid="c" * 64, managed=False)
    assert not run(adapter.cleanup(handle)).cleanupConfirmed
    assert cli.containers


def test_import_has_no_docker_side_effect():
    import codetrace_backend.docker_adapter as module

    assert module.DockerAdapter is DockerAdapter


def test_cancelled_unknown_create_is_recovered(pair):
    import asyncio

    adapter, cli = pair
    cli.create_mode = "cancel"
    with pytest.raises(asyncio.CancelledError):
        run(adapter.create(RUN_ID))
    assert not cli.containers


def test_uuid_case_matches_contract(pair):
    from codetrace_backend.docker_adapter.types import run_identity

    assert run_identity(RUN_ID.upper()) == RUN_ID.upper()
    assert "CODETRACE_RUN_ID=" + RUN_ID.upper() in pair[0].create_argv(RUN_ID.upper(), TOKEN)


@pytest.mark.parametrize("labels", [[], False, 0, ""])
def test_inspect_does_not_coerce_invalid_labels(labels):
    item = inspection()
    item["Config"]["Labels"] = labels
    with pytest.raises(DockerOutputError):
        parse_inspection(json.dumps([item]).encode())
