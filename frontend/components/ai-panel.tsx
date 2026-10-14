"use client";

import * as React from "react";
import {
  ApiError,
  api,
  isUpgradeRequired,
  pollSummaryJob,
  type Limits,
  type ProbeResult,
  type SummaryJob,
} from "@/lib/api";
import { Alert, Badge, Button, Card, Spinner } from "@/components/ui";
import { Icon, P } from "@/components/icons";
import { SummaryResultView } from "@/components/summary-result";
import { MindMapPanel } from "@/components/mind-map-panel";
import { cn } from "@/lib/utils";

const SUMMARY_STATUS_TEXT: Record<SummaryJob["status"], string> = {
  queued: "排队中",
  extracting: "正在提取字幕",
  transcribing: "正在本地识别语音",
  summarizing: "正在生成总结",
  done: "已完成",
  error: "失败",
  expired: "已过期",
};

const TRANSCRIPT_SOURCE_TEXT: Record<
  NonNullable<SummaryJob["transcript_source"]>,
  string
> = {
  manual_caption: "创作者字幕",
  automatic_caption: "自动字幕",
  local_whisper: "本地 Whisper",
};

const ACTIVE_SUMMARY_STATUSES: SummaryJob["status"][] = [
  "queued",
  "extracting",
  "transcribing",
  "summarizing",
];

function summaryFilename(title: string): string {
  const safeTitle = Array.from(title.trim(), (character) => {
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
  return `${safeTitle || "video"}-AI-summary.md`;
}

export function AIPanel({
  limits,
  probe,
  onUpgrade,
  tokenRenewalSignal,
  playableJobId,
}: {
  limits: Limits | null;
  probe: ProbeResult | null;
  onUpgrade: () => void;
  tokenRenewalSignal: number;
  /** Finished download for this source, used as the mind map's seek target. */
  playableJobId: string | null;
}) {
  const [tab, setTab] = React.useState<"summary" | "translate">("summary");
  const [sourceLang, setSourceLang] = React.useState("auto");
  const [outputLang, setOutputLang] = React.useState<"zh-CN" | "en">("zh-CN");
  const [summaryJob, setSummaryJob] = React.useState<SummaryJob | null>(null);
  const [result, setResult] = React.useState<string | null>(null);
  const [summaryStarting, setSummaryStarting] = React.useState(false);
  const [loadingResult, setLoadingResult] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [summaryError, setSummaryError] = React.useState<string | null>(null);
  const [summaryAuthPaused, setSummaryAuthPaused] = React.useState(false);
  const [translationBusy, setTranslationBusy] = React.useState(false);
  const [translationError, setTranslationError] = React.useState<string | null>(null);
  const [srt, setSrt] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);
  const summaryStopRef = React.useRef<(() => void) | null>(null);
  const summaryRequestRef = React.useRef(0);
  const autoStartedUrlRef = React.useRef<string | null>(null);
  const pausedLimitsRef = React.useRef<Limits | null>(null);
  const latestLimitsRef = React.useRef(limits);
  latestLimitsRef.current = limits;
  const pausedTokenRenewalSignalRef = React.useRef(tokenRenewalSignal);
  const latestTokenRenewalSignalRef = React.useRef(tokenRenewalSignal);
  latestTokenRenewalSignalRef.current = tokenRenewalSignal;
  const translationRequestRef = React.useRef(0);
  const copyRequestRef = React.useRef(0);
  const copyFeedbackTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const probeUrl = probe?.url;

  React.useEffect(() => {
    const summaryRequest = ++summaryRequestRef.current;
    const translationRequest = ++translationRequestRef.current;
    const copyRequest = ++copyRequestRef.current;
    summaryStopRef.current?.();
    summaryStopRef.current = null;
    if (copyFeedbackTimerRef.current !== null) {
      clearTimeout(copyFeedbackTimerRef.current);
      copyFeedbackTimerRef.current = null;
    }

    void Promise.resolve().then(() => {
      if (summaryRequestRef.current === summaryRequest) {
        setSourceLang("auto");
        setSummaryJob(null);
        setResult(null);
        setSummaryStarting(false);
        setLoadingResult(false);
        setExporting(false);
        setSummaryError(null);
        setSummaryAuthPaused(false);
        pausedLimitsRef.current = null;
        pausedTokenRenewalSignalRef.current = latestTokenRenewalSignalRef.current;
      }
      if (translationRequestRef.current === translationRequest) {
        setTranslationBusy(false);
        setTranslationError(null);
        setSrt(null);
      }
      if (copyRequestRef.current === copyRequest) {
        setCopied(false);
      }
    });

    return () => {
      summaryRequestRef.current += 1;
      translationRequestRef.current += 1;
      copyRequestRef.current += 1;
      summaryStopRef.current?.();
      summaryStopRef.current = null;
      pausedLimitsRef.current = null;
      pausedTokenRenewalSignalRef.current = latestTokenRenewalSignalRef.current;
      if (copyFeedbackTimerRef.current !== null) {
        clearTimeout(copyFeedbackTimerRef.current);
        copyFeedbackTimerRef.current = null;
      }
    };
  }, [probeUrl]);

  const enabled = limits?.ai_enabled ?? false;
  const isPro = limits?.is_pro ?? false;
  const captionLanguages = probe
    ? Array.from(new Set([...probe.subtitles, ...probe.auto_subtitles])).filter(
        (language) => language !== "auto",
      )
    : [];
  const summaryActive =
    summaryAuthPaused ||
    summaryStarting ||
    loadingResult ||
    (summaryJob !== null && ACTIVE_SUMMARY_STATUSES.includes(summaryJob.status));

  function showSummaryError(error: unknown, fallback: string) {
    if (isUpgradeRequired(error)) onUpgrade();
    setSummaryError(error instanceof ApiError ? error.message : fallback);
  }

  function pauseSummaryForAuth(error: ApiError, request: number) {
    if (summaryRequestRef.current !== request) return;
    summaryStopRef.current = null;
    pausedLimitsRef.current = latestLimitsRef.current;
    pausedTokenRenewalSignalRef.current = latestTokenRenewalSignalRef.current;
    setSummaryAuthPaused(true);
    setSummaryStarting(false);
    setLoadingResult(false);
    setSummaryError(error.message);
    onUpgrade();
  }

  async function loadSummaryResult(jobId: string, request: number) {
    if (summaryRequestRef.current !== request) return;
    setLoadingResult(true);
    try {
      const completedResult = await api.summaryResult(jobId);
      if (summaryRequestRef.current !== request) return;
      setResult(completedResult);
    } catch (error) {
      if (summaryRequestRef.current !== request) return;
      if (error instanceof ApiError && error.status === 402) {
        pauseSummaryForAuth(error, request);
      } else {
        showSummaryError(error, "总结结果读取失败，请重新生成");
      }
    } finally {
      if (summaryRequestRef.current === request) setLoadingResult(false);
    }
  }

  function startSummaryPolling(jobId: string, request: number) {
    let resultRequested = false;
    summaryStopRef.current?.();
    summaryStopRef.current = pollSummaryJob(
      jobId,
      (nextJob) => {
        if (summaryRequestRef.current !== request) return;
        setSummaryJob(nextJob);
        setSummaryAuthPaused(false);

        if (nextJob.status === "done" && !resultRequested) {
          resultRequested = true;
          setSummaryError(null);
          void loadSummaryResult(nextJob.job_id, request);
        } else if (nextJob.status === "error") {
          setSummaryError(nextJob.error ?? "AI 总结失败，请稍后重试");
        } else if (nextJob.status === "expired") {
          setSummaryError(nextJob.error ?? "总结任务已过期，请重新生成");
        }
      },
      1000,
      (error) => {
        if (summaryRequestRef.current !== request) return;
        summaryStopRef.current = null;
        setSummaryStarting(false);
        setLoadingResult(false);
        setSummaryJob((currentJob) =>
          currentJob?.job_id === jobId
            ? { ...currentJob, status: "error", error: error.message, ready: false }
            : currentJob,
        );
        showSummaryError(error, "总结状态读取失败，请重新生成");
      },
      (error) => pauseSummaryForAuth(error, request),
    );
  }

  async function runSummary() {
    if (!probe) {
      setSummaryError("请先在上方解析一个视频链接");
      return;
    }

    const request = ++summaryRequestRef.current;
    summaryStopRef.current?.();
    summaryStopRef.current = null;
    setSummaryError(null);
    setSummaryStarting(true);
    setLoadingResult(false);
    setExporting(false);
    setSummaryJob(null);
    setResult(null);
    setSummaryAuthPaused(false);
    pausedLimitsRef.current = null;
    pausedTokenRenewalSignalRef.current = latestTokenRenewalSignalRef.current;

    try {
      const created = await api.createSummaryJob({
        url: probe.url,
        title: probe.title,
        source_lang: sourceLang,
        output_lang: outputLang,
      });
      if (summaryRequestRef.current !== request) return;

      setSummaryJob(created);
      startSummaryPolling(created.job_id, request);
    } catch (error) {
      if (summaryRequestRef.current !== request) return;
      showSummaryError(error, "AI 总结启动失败，请稍后重试");
    } finally {
      if (summaryRequestRef.current === request) setSummaryStarting(false);
    }
  }

  const resumePausedSummary = React.useEffectEvent((jobId: string, request: number) => {
    if (summaryRequestRef.current !== request) return;
    setSummaryAuthPaused(false);
    pausedLimitsRef.current = null;
    pausedTokenRenewalSignalRef.current = latestTokenRenewalSignalRef.current;
    setSummaryError(null);
    startSummaryPolling(jobId, request);
  });

  React.useEffect(() => {
    const hasTokenRenewal =
      tokenRenewalSignal !== pausedTokenRenewalSignalRef.current;
    const hasProLimitsRefresh = Boolean(
      limits?.is_pro && limits !== pausedLimitsRef.current
    );
    const canResume = Boolean(
      summaryAuthPaused &&
      summaryJob &&
      (hasTokenRenewal || hasProLimitsRefresh)
    );
    if (!canResume || !summaryJob) return;
    resumePausedSummary(summaryJob.job_id, summaryRequestRef.current);
  }, [limits, summaryAuthPaused, summaryJob, tokenRenewalSignal]);

  const startAutoSummary = React.useEffectEvent(() => {
    void runSummary();
  });

  /*
   * Auto-summarize a probed video, Pro-only: each job spends Anthropic credits.
   *
   * Deferred to a microtask so it lands after the probe reset effect above, whose
   * cleanup would otherwise bump the request generation and discard this job.
   */
  React.useEffect(() => {
    if (!probeUrl || !enabled || !isPro) return;
    if (autoStartedUrlRef.current === probeUrl) return;
    autoStartedUrlRef.current = probeUrl;

    let cancelled = false;
    let started = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      started = true;
      startAutoSummary();
    });

    return () => {
      cancelled = true;
      // Release the guard only when nothing started, so a retry never duplicates.
      if (!started && autoStartedUrlRef.current === probeUrl) {
        autoStartedUrlRef.current = null;
      }
    };
  }, [enabled, isPro, probeUrl]);

  async function runTranslation() {
    if (!probe) {
      setTranslationError("请先在上方解析一个视频链接");
      return;
    }

    invalidateCopyFeedback();
    const request = ++translationRequestRef.current;
    setTranslationError(null);
    setTranslationBusy(true);
    setSrt(null);
    try {
      const translated = await api.aiTranslate(probe.url);
      if (translationRequestRef.current !== request) return;
      setSrt(translated.srt);
    } catch (error) {
      if (translationRequestRef.current !== request) return;
      if (isUpgradeRequired(error)) onUpgrade();
      setTranslationError(error instanceof ApiError ? error.message : "AI 处理失败");
    } finally {
      if (translationRequestRef.current === request) setTranslationBusy(false);
    }
  }

  async function exportSummary() {
    if (!summaryJob || !result || exporting) return;
    const request = summaryRequestRef.current;
    const jobId = summaryJob.job_id;
    setSummaryError(null);
    setExporting(true);

    try {
      const markdown = await api.summaryExport(jobId);
      if (summaryRequestRef.current !== request) return;

      const objectUrl = URL.createObjectURL(markdown);
      try {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = summaryFilename(summaryJob.title);
        anchor.click();
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    } catch (error) {
      if (summaryRequestRef.current !== request) return;
      showSummaryError(error, "总结文件下载失败，请稍后重试");
    } finally {
      if (summaryRequestRef.current === request) setExporting(false);
    }
  }

  function invalidateCopyFeedback(): number {
    const request = ++copyRequestRef.current;
    if (copyFeedbackTimerRef.current !== null) {
      clearTimeout(copyFeedbackTimerRef.current);
      copyFeedbackTimerRef.current = null;
    }
    setCopied(false);
    return request;
  }

  function copySrt() {
    if (!srt) return;
    const request = invalidateCopyFeedback();
    void navigator.clipboard.writeText(srt).then(() => {
      if (copyRequestRef.current !== request) return;
      setCopied(true);
      copyFeedbackTimerRef.current = setTimeout(() => {
        if (copyRequestRef.current !== request) return;
        copyFeedbackTimerRef.current = null;
        setCopied(false);
      }, 1600);
    });
  }

  return (
    <Card className="min-w-0 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-ink-900">
            <Icon path={P.sparkles} className="size-5 text-brand-500" fill />
            AI 助手
            {!isPro && <Badge tone="gold">Pro</Badge>}
          </h3>
          <p className="mt-1 text-sm text-ink-500">
            一键生成视频总结，或把外语字幕翻译成中文。先抓住重点，再决定是否完整观看。
          </p>
        </div>
      </div>

      <div className="mt-4 flex gap-1.5" role="group" aria-label="AI 功能">
        {(
          [
            { key: "summary", label: "视频总结", icon: P.sparkles },
            { key: "translate", label: "字幕翻译", icon: P.captions },
          ] as const
        ).map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            aria-pressed={tab === item.key}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2",
              tab === item.key
                ? "bg-brand-50 text-brand-600 ring-1 ring-brand-100"
                : "text-ink-500 hover:bg-slate-50 hover:text-ink-700",
            )}
          >
            <Icon path={item.icon} className="size-4" />
            {item.label}
          </button>
        ))}
      </div>

      {!enabled ? (
        <Alert tone="info" className="mt-4">
          <span className="inline-flex items-start gap-2">
            <Icon path={P.info} className="mt-0.5 size-4 shrink-0" />
            <span>
              AI 功能尚未配置。在 <code className="font-mono text-xs">backend/.env</code>{" "}
              中填入自己的 <code className="font-mono text-xs">ANTHROPIC_API_KEY</code>{" "}
              并重启后端即可启用，无需改动任何代码。
            </span>
          </span>
        </Alert>
      ) : tab === "summary" ? (
        <div className="mt-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-700">
                字幕语言
              </span>
              <select
                value={sourceLang}
                onChange={(event) => setSourceLang(event.target.value)}
                disabled={summaryActive}
                className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-ink-700 outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100 disabled:opacity-55"
              >
                <option value="auto">Auto</option>
                {captionLanguages.map((language) => (
                  <option key={language} value={language}>
                    {language}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-700">
                总结语言
              </span>
              <select
                value={outputLang}
                onChange={(event) =>
                  setOutputLang(event.target.value as "zh-CN" | "en")
                }
                disabled={summaryActive}
                className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-ink-700 outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100 disabled:opacity-55"
              >
                <option value="zh-CN">简体中文</option>
                <option value="en">English</option>
              </select>
            </label>
          </div>

          <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-ink-500">
            <Icon path={P.shield} className="mt-0.5 size-3.5 shrink-0" />
            <span>
              字幕文字会发送给 Anthropic；无可用字幕时，Whisper 会在服务器本地识别语音，临时音频完成后删除。
            </span>
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              onClick={runSummary}
              loading={summaryActive && !summaryAuthPaused}
              disabled={!probe || summaryAuthPaused}
            >
              <Icon path={P.sparkles} className="size-4" fill />
              {summaryJob ? "重新生成" : "生成总结"}
            </Button>
            {!probe ? (
              <span className="text-xs text-ink-500">请先解析一个视频链接</span>
            ) : !summaryJob && !isPro ? (
              <span className="text-xs text-ink-500">
                升级 Pro 后，解析视频即自动开始总结
              </span>
            ) : (
              <span className="max-w-full truncate text-xs text-ink-500 sm:max-w-[18rem]">
                当前视频：{probe.title}
              </span>
            )}
          </div>

          {summaryJob && (
            <div
              className="mt-4 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200"
              aria-live="polite"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="inline-flex items-center gap-2 text-sm font-medium text-ink-900">
                  {!summaryAuthPaused &&
                    ACTIVE_SUMMARY_STATUSES.includes(summaryJob.status) && (
                    <Spinner className="size-4 text-brand-500" />
                  )}
                  {summaryJob.status === "done" && (
                    <span className="grid size-5 place-items-center rounded-full bg-emerald-500 text-white">
                      <Icon path={P.check} className="size-3.5" />
                    </span>
                  )}
                  {(summaryJob.status === "error" ||
                    summaryJob.status === "expired") && (
                    <Icon path={P.alert} className="size-4 text-rose-500" />
                  )}
                  {summaryAuthPaused
                    ? "等待 Pro 验证"
                    : SUMMARY_STATUS_TEXT[summaryJob.status]}
                </span>
                <span className="text-xs tabular-nums text-ink-500">
                  {Math.round(Math.min(100, Math.max(0, summaryJob.percent)))}%
                </span>
              </div>

              <div
                role="progressbar"
                aria-label={SUMMARY_STATUS_TEXT[summaryJob.status]}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(
                  Math.min(100, Math.max(0, summaryJob.percent)),
                )}
                className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200"
              >
                <div
                  className="relative h-full rounded-full bg-linear-to-r from-brand-cyan to-brand-600 transition-[width] duration-500"
                  style={{
                    width: `${Math.min(100, Math.max(0, summaryJob.percent))}%`,
                  }}
                >
                  {!summaryAuthPaused &&
                    ACTIVE_SUMMARY_STATUSES.includes(summaryJob.status) && (
                    <span className="shimmer-bar absolute inset-0 rounded-full" />
                  )}
                </div>
              </div>

              {(summaryJob.transcript_source || summaryJob.transcript_lang) && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {summaryJob.transcript_source && (
                    <Badge tone="brand">
                      {TRANSCRIPT_SOURCE_TEXT[summaryJob.transcript_source]}
                    </Badge>
                  )}
                  {summaryJob.transcript_lang && (
                    <Badge tone="slate">{summaryJob.transcript_lang}</Badge>
                  )}
                </div>
              )}
            </div>
          )}

          {summaryError && (
            <Alert tone="error" className="mt-3">
              {summaryError}
            </Alert>
          )}

          {result && summaryJob && (
            <>
              <SummaryResultView
                job={summaryJob}
                markdown={result}
                exporting={exporting}
                onExport={exportSummary}
              />
              <MindMapPanel
                key={summaryJob.job_id}
                job={summaryJob}
                downloadJobId={playableJobId}
                onUpgrade={onUpgrade}
              />
            </>
          )}
        </div>
      ) : (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={runTranslation}
              loading={translationBusy}
              disabled={!probe}
            >
              <Icon path={P.sparkles} className="size-4" fill />
              翻译成中文
            </Button>
            {!probe ? (
              <span className="text-xs text-ink-500">请先解析一个视频链接</span>
            ) : (
              <span className="max-w-full truncate text-xs text-ink-500 sm:max-w-[18rem]">
                当前视频：{probe.title}
              </span>
            )}
          </div>

          {translationError && (
            <Alert tone="error" className="mt-3">
              {translationError}
            </Alert>
          )}

          {srt && (
            <div className="mt-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-ink-700">
                  翻译结果（SRT 格式，时间轴已保留）
                </span>
                <button
                  type="button"
                  onClick={copySrt}
                  className="inline-flex items-center gap-1 rounded text-xs font-medium text-brand-600 transition hover:text-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2"
                >
                  <Icon path={P.copy} className="size-3.5" />
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
              <pre className="max-h-64 overflow-auto rounded-xl bg-slate-50 p-3 font-mono text-[11px] leading-relaxed text-ink-700 ring-1 ring-slate-200">
                {srt}
              </pre>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
