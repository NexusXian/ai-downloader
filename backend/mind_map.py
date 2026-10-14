"""Mind map export serializers.

OPML is here because it is the interchange format XMind, MindNode and FreeMind all
import, so a user can keep editing a generated map in a real outliner.
"""
from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape, quoteattr


def _timestamp_label(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _node_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children")
    return [child for child in children if isinstance(child, dict)] if isinstance(
        children, list
    ) else []


def to_markdown(mind_map: dict[str, Any], title: str) -> str:
    """Render an indented Markdown outline, the format most tools accept."""
    root = mind_map.get("root")
    if not isinstance(root, dict):
        raise ValueError("mind map has no root")

    lines = [f"# {title}".rstrip(), ""]

    def walk(node: dict[str, Any], depth: int) -> None:
        stamp = _timestamp_label(node.get("timestamp"))
        suffix = f" ({stamp})" if stamp else ""
        label = str(node.get("label") or "")
        if depth == 0:
            lines.append(f"## {label}{suffix}")
            lines.append("")
        else:
            lines.append(f"{'  ' * (depth - 1)}- {label}{suffix}")
        for child in _node_children(node):
            walk(child, depth + 1)

    walk(root, 0)
    return "\n".join(lines).rstrip() + "\n"


def to_opml(mind_map: dict[str, Any], title: str) -> str:
    """Render OPML 2.0 so the map imports into XMind/MindNode/FreeMind."""
    root = mind_map.get("root")
    if not isinstance(root, dict):
        raise ValueError("mind map has no root")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        f"    <title>{escape(title)}</title>",
        "  </head>",
        "  <body>",
    ]

    def walk(node: dict[str, Any], depth: int) -> None:
        indent = "    " + "  " * depth
        label = str(node.get("label") or "")
        stamp = _timestamp_label(node.get("timestamp"))
        text = f"{label} ({stamp})" if stamp else label
        attrs = f"text={quoteattr(text)}"
        timestamp = node.get("timestamp")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            attrs += f" _timestamp={quoteattr(str(timestamp))}"
        children = _node_children(node)
        if not children:
            lines.append(f"{indent}<outline {attrs} />")
            return
        lines.append(f"{indent}<outline {attrs}>")
        for child in children:
            walk(child, depth + 1)
        lines.append(f"{indent}</outline>")

    walk(root, 0)
    lines.extend(["  </body>", "</opml>", ""])
    return "\n".join(lines)
