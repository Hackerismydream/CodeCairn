"""Provider-neutral failures exposed by the memory runtime."""


class TraceImportError(ValueError):
    """Raised when an agent trace cannot be imported safely."""
