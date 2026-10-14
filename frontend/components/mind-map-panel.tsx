"use client";

import * as React from "react";

import { Icon, P } from "@/components/icons";
import { MindMapCanvas } from "@/components/mind-map-view";
import { VideoPlayer, type PlayerHandle } from "@/components/video-player";
import { Alert, Badge, Button, Spinner } from "@/components/ui";
import {
  ApiError,
  api,
  isUpgradeRequired,
  pollMindMap,
  type MindMap,
  type MindMapExportFormat,
  type SummaryJob,
} from "@/lib/api";
import { mindMapFilename } from "@/lib/mind-map";

const STATUS_TEXT: Record<SummaryJob["mind_map_status"], string> = {
  idle: "尚未生成",
  queued: "排队中",
  generating: "正在生成思维导图",
  done: "已完成",
  error: "失败",
};

const EXPORTS: { format: MindMapExportFormat; label: string; ext: string }[] = [
  { format: "json", label: "JSON", ext: "json" },
  { format: "markdown", label: "Markdown", ext: "md" },
  { format: "opml", label: "OPML", ext: "opml" },
];

function saveBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.click();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/**
 * Serialize the rendered canvas.
 *
 * markmap draws real SVG, so the image exports come from the live DOM rather than
 * a second render on the server. Inline styles are copied because the visual
 * styling lives in stylesheets the exported file would not carry.
 */
function serializeCanvas(container: HTMLElement): string | null {
  const source = container.querySelector("svg");
  if (!source) return null;

  const clone = source.cloneNode(true) as SVGSVGElement;
  const { width, height } = source.getBoundingClientRect();
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(Math.round(width)));
  clone.setAttribute("height", String(Math.round(height)));
  clone.querySelectorAll("foreignObject div").forEach((node) => {
    (node as HTMLElement).style.font = "500 14px system-ui, sans-serif";
    (node as HTMLElement).style.color = "#0f172a";
  });
  const background = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "rect",
  );
  background.setAttribute("width", "100%");
  background.setAttribute("height", "100%");
  background.setAttribute("fill", "#ffffff");
  clone.insertBefore(background, clone.firstChild);

  return new XMLSerializer().serializeToString(clone);
}

async function canvasToPng(svgMarkup: string): Promise<Blob> {
  const source = new Blob([svgMarkup], { type: "image/svg+xml;charset=utf-8" });
  const objectUrl = URL.createObjectURL(source);
  try {
    const image = new Image();
    image.decoding = "sync";
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("png"));
      image.src = objectUrl;
    });

    const scale = Math.min(2, window.devicePixelRatio || 1);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("png");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.drawImage(image, 0, 0);

    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("png"))),
        "image/png",
      );
    });
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export function MindMapPanel({
  job,
  downloadJobId,
  onUpgrade,
}: {
  job: SummaryJob;
  downloadJobId: string | null;
  onUpgrade: () => void;
}) {
  const [status, setStatus] = React.useState<SummaryJob["mind_map_status"]>(
    job.mind_map_status,
  );
  const [percent, setPercent] = React.useState(job.mind_map_percent);
  const [mindMap, setMindMap] = React.useState<MindMap | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [starting, setStarting] = React.useState(false);
  const [busyExport, setBusyExport] = React.useState<string | null>(null);
  const stopRef = React.useRef<(() => void) | null>(null);
  const requestRef = React.useRef(0);
  const playerRef = React.useRef<PlayerHandle | null>(null);
  const canvasRef = React.useRef<HTMLDivElement | null>(null);
  const jobId = job.job_id;

  React.useEffect(() => {
    const request = ++requestRef.current;
    stopRef.current?.();
    stopRef.current = null;

    void Promise.resolve().then(() => {
      if (requestRef.current !== request) return;
      setStatus("idle");
      setPercent(0);
      setMindMap(null);
      setError(null);
      setStarting(false);
      setBusyExport(null);
    });

    return () => {
      requestRef.current += 1;
      stopRef.current?.();
      stopRef.current = null;
    };
  }, [jobId]);

  const active = starting || status === "queued" || status === "generating";

  function reportError(err: unknown, fallback: string) {
    if (isUpgradeRequired(err)) onUpgrade();
    setError(err instanceof ApiError ? err.message : fallback);
  }

  async function loadResult(request: number) {
    try {
      const loaded = await api.mindMap(jobId);
      if (requestRef.current !== request) return;
      setMindMap(loaded);
      setError(null);
    } catch (err) {
      if (requestRef.current !== request) return;
      reportError(err, "思维导图读取失败，请重新生成");
    }
  }

  async function generate() {
    const request = ++requestRef.current;
    stopRef.current?.();
    stopRef.current = null;
    setError(null);
    setStarting(true);
    setMindMap(null);
    setPercent(0);
    setStatus("queued");

    try {
      const created = await api.createMindMap(jobId);
      if (requestRef.current !== request) return;
      setStatus(created.mind_map_status);
      setPercent(created.mind_map_percent);

      stopRef.current = pollMindMap(
        jobId,
        (next) => {
          if (requestRef.current !== request) return;
          setStatus(next.mind_map_status);
          setPercent(next.mind_map_percent);
          if (next.mind_map_status === "done") {
            void loadResult(request);
          } else if (next.mind_map_status === "error") {
            setError(next.mind_map_error ?? "思维导图生成失败，请稍后重试");
          }
        },
        1000,
        (err) => {
          if (requestRef.current !== request) return;
          stopRef.current = null;
          setStatus("error");
          reportError(err, "思维导图状态读取失败，请重新生成");
        },
      );
    } catch (err) {
      if (requestRef.current !== request) return;
      setStatus("error");
      reportError(err, "思维导图生成启动失败，请稍后重试");
    } finally {
      if (requestRef.current === request) setStarting(false);
    }
  }

  async function exportData(format: MindMapExportFormat, ext: string) {
    if (busyExport) return;
    const request = requestRef.current;
    setBusyExport(format);
    setError(null);
    try {
      const blob = await api.mindMapExport(jobId, format);
      if (requestRef.current !== request) return;
      saveBlob(blob, mindMapFilename(job.title, ext));
    } catch (err) {
      if (requestRef.current !== request) return;
      reportError(err, "思维导图导出失败，请稍后重试");
    } finally {
      if (requestRef.current === request) setBusyExport(null);
    }
  }

  async function exportImage(kind: "svg" | "png") {
    if (busyExport || !canvasRef.current) return;
    setBusyExport(kind);
    setError(null);
    try {
      const markup = serializeCanvas(canvasRef.current);
      if (!markup) throw new Error("canvas");
      if (kind === "svg") {
        saveBlob(
          new Blob([markup], { type: "image/svg+xml;charset=utf-8" }),
          mindMapFilename(job.title, "svg"),
        );
      } else {
        saveBlob(await canvasToPng(markup), mindMapFilename(job.title, "png"));
      }
    } catch {
      setError("图片导出失败，请重试或改用 JSON / Markdown / OPML 导出");
    } finally {
      setBusyExport(null);
    }
  }

  return (
    <section className="animate-rise mt-5 overflow-hidden rounded-xl border border-brand-100 bg-white shadow-soft">
      <header className="flex flex-col gap-4 bg-linear-to-br from-brand-50/90 to-white p-4 sm:flex-row sm:items-start sm:justify-between sm:p-5">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brand-500">
            AI Mind Map
          </p>
          <h4 className="mt-1 text-base font-semibold leading-snug text-ink-900">
            视频结构一览
          </h4>
          <p className="mt-1 text-sm text-ink-500">
            用导图看清全片脉络。带时间点的节点可点击，直接跳到视频对应位置。
          </p>
          {mindMap && (
            <div className="mt-2 flex flex-wrap gap-2">
              <Badge tone="brand">{mindMap.node_count} 个节点</Badge>
              {downloadJobId && <Badge tone="slate">可跳转播放</Badge>}
            </div>
          )}
        </div>
        <Button
          type="button"
          onClick={generate}
          loading={active}
          disabled={active}
          className="shrink-0"
        >
          <Icon path={P.mindmap} className="size-4" />
          {mindMap ? "重新生成" : "生成思维导图"}
        </Button>
      </header>

      <div className="p-4 sm:p-5">
        {active && (
          <div
            className="rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200"
            aria-live="polite"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="inline-flex items-center gap-2 text-sm font-medium text-ink-900">
                <Spinner className="size-4 text-brand-500" />
                {STATUS_TEXT[status]}
              </span>
              <span className="text-xs tabular-nums text-ink-500">
                {Math.round(Math.min(100, Math.max(0, percent)))}%
              </span>
            </div>
            <div
              role="progressbar"
              aria-label={STATUS_TEXT[status]}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(Math.min(100, Math.max(0, percent)))}
              className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200"
            >
              <div
                className="h-full rounded-full bg-linear-to-r from-brand-cyan to-brand-600 transition-[width] duration-500"
                style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
              />
            </div>
          </div>
        )}

        {error && (
          <Alert tone="error" className={active ? "mt-3" : undefined}>
            {error}
          </Alert>
        )}

        {!active && !mindMap && !error && (
          <p className="text-sm text-ink-500">
            总结已完成。点击上方按钮生成导图，把要点整理成可展开的结构。
          </p>
        )}

        {mindMap && (
          <>
            <div ref={canvasRef} className="mt-1">
              <MindMapCanvas
                mindMap={mindMap}
                onSeek={
                  downloadJobId
                    ? (seconds) => playerRef.current?.seek(seconds)
                    : null
                }
              />
            </div>

            {downloadJobId ? (
              <VideoPlayer
                ref={playerRef}
                jobId={downloadJobId}
                title={job.title}
              />
            ) : (
              <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs text-ink-500 ring-1 ring-slate-200">
                先在上方下载这个视频，导图节点即可点击跳转到对应时间点。
              </p>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-ink-700">导出：</span>
              {EXPORTS.map((item) => (
                <Button
                  key={item.format}
                  type="button"
                  variant="outline"
                  size="sm"
                  loading={busyExport === item.format}
                  disabled={busyExport !== null}
                  onClick={() => void exportData(item.format, item.ext)}
                >
                  <Icon path={P.download} className="size-4" />
                  {item.label}
                </Button>
              ))}
              {(["svg", "png"] as const).map((kind) => (
                <Button
                  key={kind}
                  type="button"
                  variant="outline"
                  size="sm"
                  loading={busyExport === kind}
                  disabled={busyExport !== null}
                  onClick={() => void exportImage(kind)}
                >
                  <Icon path={P.download} className="size-4" />
                  {kind.toUpperCase()}
                </Button>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
