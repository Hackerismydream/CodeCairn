"""Provider-neutral failures exposed by the memory runtime."""


class TraceImportError(ValueError):
    """Raised when an agent trace cannot be imported safely."""


class TraceParseError(TraceImportError):
    """Raised when a provider trace cannot be parsed safely."""


class SourceRewritten(TraceImportError):
    """A committed source prefix was changed or truncated."""

    code = "source_rewritten"
