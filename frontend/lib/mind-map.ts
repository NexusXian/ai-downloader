import type { MindMapNode } from "@/lib/api";
import { formatTimestamp } from "@/lib/video";

/** markmap renders node content as HTML, so AI text is escaped before it gets there. */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Flat id -> timestamp map, so a click on rendered SVG can resolve its node. */
export function collectTimestamps(node: MindMapNode): Map<string, number> {
  const found = new Map<string, number>();
  const walk = (current: MindMapNode) => {
    if (typeof current.timestamp === "number") {
      found.set(current.id, current.timestamp);
    }
    for (const child of current.children ?? []) walk(child);
  };
  walk(node);
  return found;
}

export interface MarkmapNode {
  content: string;
  payload: { nodeId: string };
  children: MarkmapNode[];
}

export function toMarkmapTree(node: MindMapNode): MarkmapNode {
  const stamp =
    typeof node.timestamp === "number"
      ? `<span class="mm-time" data-node="${escapeHtml(node.id)}">${escapeHtml(
          formatTimestamp(node.timestamp),
        )}</span>`
      : "";
  return {
    content: `${escapeHtml(node.label)}${stamp}`,
    payload: { nodeId: node.id },
    children: (node.children ?? []).map(toMarkmapTree),
  };
}

/** Resolve a clicked SVG element to the seek time of its owning node. */
export function seekTimeForElement(
  target: Element | null,
  timestamps: Map<string, number>,
): number | null {
  const stamp = target?.closest?.("[data-node]");
  const nodeId = stamp?.getAttribute("data-node");
  if (!nodeId) return null;
  const seconds = timestamps.get(nodeId);
  return seconds === undefined ? null : seconds;
}

export function mindMapFilename(title: string, ext: string): string {
  const cleaned = Array.from(title.trim(), (character) => {
    const code = character.charCodeAt(0);
    return code < 32 ||
      (code >= 127 && code <= 159) ||
      '<>:"/\\|?*'.includes(character)
      ? "_"
      : character;
  })
    .join("")
    .replace(/\s+/g, " ")
    .replace(/^[. ]+|[. ]+$/g, "")
    .slice(0, 120);
  return `${cleaned || "video"}-mindmap.${ext}`;
}
