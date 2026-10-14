import assert from "node:assert/strict";
import { test } from "node:test";

import {
  collectTimestamps,
  escapeHtml,
  mindMapFilename,
  seekTimeForElement,
  toMarkmapTree,
} from "../lib/mind-map.ts";

const node = (id, label, timestamp, children = []) => ({
  id,
  label,
  timestamp,
  children,
});

test("escapeHtml neutralizes markup in model output", () => {
  assert.equal(
    escapeHtml('<img src=x onerror="alert(1)">'),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
  );
  assert.equal(escapeHtml("a & b"), "a &amp; b");
  assert.equal(escapeHtml("it's"), "it&#39;s");
});

test("toMarkmapTree escapes every label it renders", () => {
  const tree = toMarkmapTree(
    node("n1", "<script>root</script>", null, [
      node("n2", "child & more", 5),
    ]),
  );

  assert.ok(!tree.content.includes("<script>"));
  assert.ok(tree.content.includes("&lt;script&gt;"));
  assert.ok(tree.children[0].content.includes("child &amp; more"));
});

test("toMarkmapTree renders a timestamp badge only when present", () => {
  const withStamp = toMarkmapTree(node("n1", "topic", 65, []));
  const without = toMarkmapTree(node("n1", "topic", null, []));

  assert.ok(withStamp.content.includes('data-node="n1"'));
  assert.ok(withStamp.content.includes("1:05"));
  assert.ok(!without.content.includes("mm-time"));
});

test("toMarkmapTree preserves structure and node ids", () => {
  const tree = toMarkmapTree(
    node("n1", "root", null, [
      node("n2", "a", 1, [node("n3", "a1", 2)]),
      node("n4", "b", null),
    ]),
  );

  assert.equal(tree.payload.nodeId, "n1");
  assert.equal(tree.children.length, 2);
  assert.equal(tree.children[0].children[0].payload.nodeId, "n3");
  assert.equal(tree.children[1].children.length, 0);
});

test("toMarkmapTree tolerates a missing children array", () => {
  const tree = toMarkmapTree({ id: "n1", label: "solo", timestamp: null });

  assert.deepEqual(tree.children, []);
});

test("collectTimestamps indexes only timestamped nodes", () => {
  const found = collectTimestamps(
    node("n1", "root", null, [
      node("n2", "a", 12.5, [node("n3", "a1", null)]),
      node("n4", "b", 0),
    ]),
  );

  assert.deepEqual([...found.entries()], [["n2", 12.5], ["n4", 0]]);
});

test("seekTimeForElement resolves the clicked node", () => {
  const timestamps = new Map([["n2", 42]]);
  const element = {
    closest: (selector) =>
      selector === "[data-node]"
        ? { getAttribute: () => "n2" }
        : null,
  };

  assert.equal(seekTimeForElement(element, timestamps), 42);
});

test("seekTimeForElement ignores clicks outside a timestamp badge", () => {
  const timestamps = new Map([["n2", 42]]);

  assert.equal(seekTimeForElement(null, timestamps), null);
  assert.equal(
    seekTimeForElement({ closest: () => null }, timestamps),
    null,
  );
  assert.equal(
    seekTimeForElement(
      { closest: () => ({ getAttribute: () => "unknown" }) },
      timestamps,
    ),
    null,
  );
});

test("seekTimeForElement allows a zero-second node", () => {
  assert.equal(
    seekTimeForElement(
      { closest: () => ({ getAttribute: () => "n1" }) },
      new Map([["n1", 0]]),
    ),
    0,
  );
});

test("mindMapFilename strips path and control characters", () => {
  assert.equal(mindMapFilename("a/b\\c:d", "json"), "a_b_c_d-mindmap.json");
  assert.equal(mindMapFilename("  ..hidden..  ", "md"), "hidden-mindmap.md");
  assert.equal(mindMapFilename("", "opml"), "video-mindmap.opml");
  assert.equal(mindMapFilename("测试视频", "svg"), "测试视频-mindmap.svg");
});

test("mindMapFilename caps very long titles", () => {
  const name = mindMapFilename("x".repeat(400), "png");

  assert.ok(name.length <= 120 + "-mindmap.png".length);
  assert.ok(name.endsWith("-mindmap.png"));
});
