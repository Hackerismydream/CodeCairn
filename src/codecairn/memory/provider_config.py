"""Typed configuration failure shared by retrieval provider resolution and use."""

from __future__ import annotations

RETRIEVAL_REMEDIATION = (
    "export DASHSCOPE_API_KEY=<key> for the dashscope profile, "
    "or set CODECAIRN_RETRIEVAL_PROFILE=fastembed to run without a provider key"
)


class ProviderConfigurationError(ValueError):
    """A retrieval provider cannot serve requests under the current configuration."""

    def __init__(
        self,
        message: str,
        *,
        remediation: str = RETRIEVAL_REMEDIATION,
    ) -> None:
        super().__init__(message)
        self.remediation = remediation
