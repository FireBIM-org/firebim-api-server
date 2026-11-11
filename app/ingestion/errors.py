"""Processing diagnostics shared with credential-free subprocesses."""


class ProcessingError(Exception):
    def __init__(self, code: str, message: str, *, transient: bool = False, metrics=None):
        super().__init__(message)
        self.code = code
        self.transient = transient
        self.metrics = metrics or {}
