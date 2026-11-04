"""The shared Message contract (identical across the Slack and ClickUp clients).

`Message.to_dict()` emits exactly the contract's fields -- no more, no fewer --
with every id as a string. Changing this shape is a cross-slice contract
change, not a local refactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ContainerKind = Literal["channel", "dm", "mpim", "task", "chat"]

#: The contract's field set, in contract order. Pinned by hand in tests.
MESSAGE_FIELDS = (
    "source",
    "id",
    "thread_id",
    "container",
    "author",
    "ts",
    "text",
    "permalink",
    "is_mention",
    "is_dm",
    "awaiting_reply",
)


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
    source: Literal["clickup"] = "clickup"

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

    def to_dict(self) -> dict[str, str]:
        return {"source": "clickup", "id": self.id, "name": self.name}
