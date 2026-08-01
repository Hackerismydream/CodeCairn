from __future__ import annotations

from dataclasses import asdict

from codecairn_hub_api.queries import HubLibraryApplication


class HubGovernanceModule:
    """Expose one server-bound Person Library mutation."""

    def __init__(self, library: HubLibraryApplication) -> None:
        self._library = library

    def promote_preference(self, memory_id: str) -> dict[str, object]:
        receipt = self._library.promote_preference(memory_id)
        snapshot = self._library.library()
        return {
            "schema_version": 1,
            "library_context": {
                "person_id": snapshot.person.person_id,
                "current_repository_key": snapshot.repository_key,
                "active_scopes": snapshot.active_scopes,
            },
            "receipt": asdict(receipt),
        }
