class TraceImportError(ValueError):
    """Raised when an agent trace cannot be imported safely."""


class TraceParseError(TraceImportError):
    """Raised when a provider trace cannot be parsed safely."""


class SourceRewritten(TraceImportError):
    """A committed source prefix was changed or truncated."""

    code = "source_rewritten"


class IndexNotReady(RuntimeError):
    code = "index_not_ready"
    remediation = "Run `codecairn index sync` or `codecairn index rebuild`."


class ProviderConfigurationError(ValueError):
    remediation = "Configure the explicit retrieval profile and retry."
