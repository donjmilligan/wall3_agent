"""The shared Message contract, as frozen dataclasses.

``Message.to_dict()`` emits exactly the contract's field set -- no more, no
less -- and ``tests/test_contract_shape.py`` pins it against a hand-written
copy of the contract rather than against this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ContainerKind = Literal["channel", "dm", "mpim"]


@dataclass(frozen=True)
class Container:
    kind: ContainerKind
    id: str
    name: str


@dataclass(frozen=True)
class Author:
    id: str
    name: str


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str | None
    container: Container
    author: Author
    ts: str
    text: str
    permalink: str
    is_mention: bool
    is_dm: bool
    awaiting_reply: bool
    source: Literal["slack"] = "slack"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "id": self.id,
            "thread_id": self.thread_id,
            "container": {
                "kind": self.container.kind,
                "id": self.container.id,
                "name": self.container.name,
            },
            "author": {"id": self.author.id, "name": self.author.name},
            "ts": self.ts,
            "text": self.text,
            "permalink": self.permalink,
            "is_mention": self.is_mention,
            "is_dm": self.is_dm,
            "awaiting_reply": self.awaiting_reply,
        }


@dataclass(frozen=True)
class Whoami:
    id: str
    name: str
    source: Literal["slack"] = "slack"

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "id": self.id, "name": self.name}
