"""Internal operation errors, never Completion/RunStatus/user diagnostics."""


class DockerOperationError(RuntimeError):
    reason = "RUNNER_FAILURE"

    def __init__(self, operation: str) -> None:
        super().__init__(f"Docker operation failed: {operation}")
        self.operation = operation


class DockerUnavailableError(DockerOperationError):
    reason = "DOCKER_UNAVAILABLE"

    def __init__(self, kind: str = "daemon") -> None:
        super().__init__("availability")
        self.kind = kind  # executable / daemon


class ImageUnavailableError(DockerOperationError):
    reason = "IMAGE_UNAVAILABLE"


class DockerCommandTimeout(DockerOperationError):
    pass


class DockerOutputError(DockerOperationError):
    pass


class ForeignContainerError(DockerOperationError):
    pass


class ContainerConflictError(DockerOperationError):
    def __init__(self, managed: bool) -> None:
        super().__init__("create conflict")
        self.managed = managed


class CreateOutcomeError(DockerOperationError):
    def __init__(self, cleanup_confirmed: bool, cause_reason: str = "RUNNER_FAILURE") -> None:
        super().__init__("unknown create outcome")
        self.cleanupConfirmed = cleanup_confirmed
        self.causeReason = cause_reason
        self.reason = cause_reason


class CleanupError(DockerOperationError):
    reason = "CLEANUP_FAILED"
