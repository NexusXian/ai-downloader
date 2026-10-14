import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const downloader = read("components/downloader.tsx");
const api = read("lib/api.ts");
const aiPanel = read("components/ai-panel.tsx");
const summaryResult = read("components/summary-result.tsx");
const page = read("app/page.tsx");
const pricing = read("components/pricing.tsx");

const tokenStoredAt = pricing.indexOf("setToken(result.token)");
const redeemedCallbackAt = pricing.indexOf("onRedeemed()", tokenStoredAt);
assert.ok(
  tokenStoredAt >= 0 && redeemedCallbackAt > tokenStoredAt,
  "Successful redemption must store the token before signaling renewal",
);

assert.match(
  api,
  /interface SummaryJob[\s\S]*created_at:\s*number;[\s\S]*updated_at:\s*number;/,
  "Summary job types must expose numeric public timestamps",
);
assert.doesNotMatch(
  api,
  /interface SummaryResult/,
  "AI summaries must not require a structured JSON result",
);
assert.match(
  api,
  /async function requestText[\s\S]*return res\.text\(\)[\s\S]*summaryResult:[\s\S]*requestText/,
  "The summary result client must read raw Markdown text",
);
assert.match(
  aiPanel,
  /useState<string \| null>\(null\)/,
  "The AI panel must keep the completed summary as Markdown text",
);
assert.match(
  summaryResult,
  /from "react-markdown"[\s\S]*<ReactMarkdown/,
  "The summary result must render Markdown",
);
const markdownElementsStart = summaryResult.indexOf("const MARKDOWN_ELEMENTS");
const markdownElementsEnd = summaryResult.indexOf("as const;", markdownElementsStart);
assert.ok(
  markdownElementsStart >= 0 && markdownElementsEnd > markdownElementsStart,
  "The Markdown renderer must define an element allowlist",
);
assert.doesNotMatch(
  summaryResult.slice(markdownElementsStart, markdownElementsEnd),
  /["']img["']/,
  "The Markdown renderer must not load AI-generated images",
);

assert.match(
  downloader,
  /requestGenerationRef\s*=\s*React\.useRef\(0\)/,
  "Downloader must own a request generation",
);
assert.match(
  downloader,
  /function handleUrlChange[\s\S]*?requestGenerationRef\.current\s*\+=\s*1/,
  "Changing the input must invalidate pending requests",
);
assert.ok(
  downloader.match(/requestGenerationRef\.current\s*!==\s*request/g)?.length >= 4,
  "Probe and download creation completions must be generation-guarded",
);
assert.match(
  downloader,
  /pollJob\(created\.job_id,\s*\(nextJob\)\s*=>\s*\{[\s\S]*?requestGenerationRef\.current\s*!==\s*request/,
  "Download polling updates must remain bound to their request generation",
);
assert.match(
  page,
  /<AIPanel\s+key=\{probe\?\.url\s*\?\?/,
  "AI state must be synchronously keyed to the current probe URL",
);
assert.match(
  page,
  /summarySlot=\{\s*<AIPanel/,
  "The summary must render beside the probed video, not in a separate section",
);
assert.match(
  downloader,
  /summarySlot\?:\s*React\.ReactNode[\s\S]*lg:grid-cols-2[\s\S]*\{summarySlot\s*\?/,
  "Downloader must place the summary slot in a two-column workspace",
);
assert.match(
  page,
  /tokenRenewalSignal[\s\S]*setTokenRenewalSignal[\s\S]*handleRedeemed[\s\S]*setTokenRenewalSignal[\s\S]*refreshLimits/,
  "Home must signal token renewal directly while retaining limits refresh",
);
assert.match(
  page,
  /<AIPanel[\s\S]*tokenRenewalSignal=\{tokenRenewalSignal\}[\s\S]*<Pricing[\s\S]*onRedeemed=\{handleRedeemed\}/,
  "Home must wire the redemption signal from Pricing to AIPanel",
);
assert.match(
  api,
  /err instanceof ApiError\s*&&\s*err\.status === 402[\s\S]*?onAuthRequired\?\.\(err\)/,
  "Summary polling must expose 402 as an authentication pause",
);
assert.match(
  aiPanel,
  /summaryAuthPaused[\s\S]*pausedLimitsRef/,
  "AI summary state must retain an explicit authentication pause",
);
assert.match(
  aiPanel,
  /latestLimitsRef\.current\s*=\s*limits[\s\S]*pausedLimitsRef\.current\s*=\s*latestLimitsRef\.current/,
  "Authentication pauses must capture the latest limits result",
);
assert.match(
  aiPanel,
  /limits\?\.is_pro[\s\S]*limits !== pausedLimitsRef\.current[\s\S]*resumePausedSummary/,
  "A refreshed Pro limits result must resume the paused summary",
);
assert.match(
  aiPanel,
  /hasTokenRenewal\s*=\s*tokenRenewalSignal\s*!==\s*pausedTokenRenewalSignalRef\.current/,
  "AIPanel must detect token renewal without a limits response",
);
assert.match(
  aiPanel,
  /hasProLimitsRefresh[\s\S]*hasTokenRenewal\s*\|\|\s*hasProLimitsRefresh/,
  "Token renewal must independently resume an auth-paused job",
);
const autoStart = aiPanel.slice(aiPanel.indexOf("const startAutoSummary"));
assert.match(
  autoStart,
  /if \(!probeUrl \|\| !enabled \|\| !isPro\) return;/,
  "Automatic summaries must stay gated on a configured AI key and a Pro token",
);
assert.match(
  autoStart,
  /autoStartedUrlRef\.current === probeUrl\) return;[\s\S]*autoStartedUrlRef\.current = probeUrl;/,
  "A probed URL must auto-start at most one summary job",
);

const resumeStart = aiPanel.indexOf("const resumePausedSummary");
const resumeEnd = aiPanel.indexOf("\n  React.useEffect(()", resumeStart);
assert.ok(resumeStart >= 0 && resumeEnd > resumeStart, "Paused summary resume must exist");
const resumeContract = aiPanel.slice(resumeStart, resumeEnd);
assert.match(
  resumeContract,
  /startSummaryPolling\(jobId, request\)/,
  "Renewal must poll the existing summary job",
);
assert.doesNotMatch(
  resumeContract,
  /createSummaryJob/,
  "Renewal must not create duplicate AI work",
);

const mindMapPanel = read("components/mind-map-panel.tsx");
const mindMapView = read("components/mind-map-view.tsx");
const mindMapLib = read("lib/mind-map.ts");
const videoPlayer = read("components/video-player.tsx");

assert.match(
  api,
  /interface MindMapNode[\s\S]*id:\s*string;[\s\S]*label:\s*string;[\s\S]*timestamp:\s*number \| null;/,
  "Mind map nodes must carry a server-assigned id, label and nullable timestamp",
);
assert.match(
  api,
  /mind_map_status:\s*MindMapStatus;[\s\S]*mind_map_ready:\s*boolean;/,
  "Summary job types must expose mind map lifecycle state",
);
assert.match(
  api,
  /createMindMap:[\s\S]*method:\s*"POST"/,
  "Mind map generation must be an explicit POST, never a side effect of polling",
);
assert.match(
  mindMapPanel,
  /function generate\(\)[\s\S]*api\.createMindMap/,
  "The mind map must be generated from a user action",
);
assert.doesNotMatch(
  mindMapPanel,
  /React\.useEffect\([^)]*\)\s*=>\s*\{[^}]*api\.createMindMap/,
  "Mind map generation must not start automatically on mount",
);
assert.match(
  mindMapLib,
  /function escapeHtml[\s\S]*replace\(\/&\/g[\s\S]*replace\(\/</g,
  "Mind map labels must be HTML-escaped before markmap renders them",
);
const treeStart = mindMapLib.indexOf("export function toMarkmapTree");
assert.ok(treeStart >= 0, "Mind map tree builder must exist");
assert.match(
  mindMapLib.slice(treeStart),
  /escapeHtml\(node\.label\)/,
  "Node labels must pass through escapeHtml on every render path",
);
assert.doesNotMatch(
  mindMapView,
  /dangerouslySetInnerHTML/,
  "The mind map renderer must not inject HTML directly",
);
assert.match(
  videoPlayer,
  /api\.playUrl\(jobId\)/,
  "The player must stream from the inline playback endpoint so ranges work",
);
assert.match(
  mindMapPanel,
  /downloadJobId[\s\S]*playerRef\.current\?\.seek\(seconds\)/,
  "Timestamped nodes must seek the player when a download is available",
);

console.log("AI frontend contracts passed");
