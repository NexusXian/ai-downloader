"use client";

import * as React from "react";
import {
  ApiError,
  api,
  formatBytes,
  formatDuration,
  formatEta,
  formatSpeed,
  isIOS,
  isUpgradeRequired,
  pollJob,
  type FormatOption,
  type Job,
  type JobMode,
  type Limits,
  type ProbeResult,
} from "@/lib/api";
import { Alert, Badge, Button, Card, Spinner } from "@/components/ui";
import { Icon, P } from "@/components/icons";
import { cn } from "@/lib/utils";

const STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  probing: "正在解析链接",
  downloading: "正在下载",
  merging: "正在合并高清音视频",
  converting: "正在转换格式",
  done: "下载完成",
  error: "下载失败",
  expired: "已过期",
};

export function Downloader({
  limits,
  onUpgrade,
  onProbed,
  onPlayableJob,
  summarySlot,
}: {
  limits: Limits | null;
  onUpgrade: () => void;
  onProbed?: (probe: ProbeResult | null) => void;
  /** Reports a finished video download, which the mind map uses to seek. */
  onPlayableJob?: (jobId: string | null) => void;
  /** Rendered beside the probed video so details and summary share one screen. */
  summarySlot?: React.ReactNode;
}) {
  const [url, setUrl] = React.useState("");
  const [probing, setProbing] = React.useState(false);
  const [probe, setProbe] = React.useState<ProbeResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [mode, setMode] = React.useState<JobMode>("video");
  const [height, setHeight] = React.useState<number | null>(null);
  const [subtitleLang, setSubtitleLang] = React.useState<string | null>(null);
  const [job, setJob] = React.useState<Job | null>(null);
  const [starting, setStarting] = React.useState(false);
  const stopRef = React.useRef<(() => void) | null>(null);
  const requestGenerationRef = React.useRef(0);

  React.useEffect(() => () => {
    requestGenerationRef.current += 1;
    stopRef.current?.();
  }, []);

  const reportPlayableJob = React.useEffectEvent((jobId: string | null) => {
    onPlayableJob?.(jobId);
  });

  React.useEffect(() => {
    // Only a finished video file can back the player; audio and subtitle jobs
    // produce files a <video> element cannot seek meaningfully.
    const playable =
      job !== null && job.status === "done" && job.mode === "video"
        ? job.job_id
        : null;
    reportPlayableJob(playable);
  }, [job]);

  const maxHeight = limits?.max_height ?? 720;

  const reset = () => {
    stopRef.current?.();
    stopRef.current = null;
    setJob(null);
    setError(null);
  };

  function handleUrlChange(nextUrl: string) {
    setUrl(nextUrl);
    requestGenerationRef.current += 1;
    setProbing(false);
    setStarting(false);
    setProbe(null);
    reset();
    onProbed?.(null);
  }

  async function handleProbe(e?: React.FormEvent) {
    e?.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) {
      setError("请先粘贴视频链接");
      return;
    }
    const request = ++requestGenerationRef.current;
    reset();
    setProbing(true);
    try {
      const result = await api.probe(trimmed);
      if (requestGenerationRef.current !== request) return;
      // Re-parsing the same link keeps the previous result object. Consumers key
      // AI state by probe URL, so replacing an identical result would restart a
      // running summary and spend AI credits again.
      setProbe((current) => (current?.url === result.url ? current : result));
      onProbed?.(result);
      // Default to the best quality the tier allows.
      const allowed = result.formats.filter((f) => f.height <= maxHeight);
      setHeight(allowed[0]?.height ?? null);
      const langs = [...result.subtitles, ...result.auto_subtitles];
      setSubtitleLang(
        langs.find((l) => l.startsWith("zh")) ?? langs.find((l) => l.startsWith("en")) ?? langs[0] ?? null,
      );
    } catch (err) {
      if (requestGenerationRef.current !== request) return;
      setProbe(null);
      onProbed?.(null);
      setError(err instanceof ApiError ? err.message : "解析失败，请稍后重试");
    } finally {
      if (requestGenerationRef.current === request) setProbing(false);
    }
  }

  async function handleStart(nextMode: JobMode) {
    if (!probe) return;
    const currentProbe = probe;
    const request = ++requestGenerationRef.current;
    setMode(nextMode);
    reset();
    setStarting(true);
    try {
      const created = await api.createJob({
        url: currentProbe.url,
        mode: nextMode,
        height: nextMode === "video" ? height : null,
        subtitle_lang: nextMode === "subtitle" ? subtitleLang : null,
      });
      if (requestGenerationRef.current !== request) return;
      setJob(created);
      stopRef.current = pollJob(created.job_id, (nextJob) => {
        if (requestGenerationRef.current !== request) return;
        setJob(nextJob);
      });
    } catch (err) {
      if (requestGenerationRef.current !== request) return;
      if (isUpgradeRequired(err)) {
        onUpgrade();
        setError(err instanceof ApiError ? err.message : null);
      } else {
        setError(err instanceof ApiError ? err.message : "启动下载失败");
      }
    } finally {
      if (requestGenerationRef.current === request) setStarting(false);
    }
  }

  const busy =
    job !== null &&
    ["queued", "probing", "downloading", "merging", "converting"].includes(job.status);

  // Widen only once there is something to place beside the video: the empty hero
  // stays a narrow, centred search box.
  const hasWorkspace = probe !== null;

  return (
    <div
      className={cn(
        "mx-auto w-full transition-[max-width] duration-500",
        hasWorkspace ? "max-w-6xl" : "max-w-3xl",
      )}
    >
      <div className="mx-auto w-full max-w-3xl">
        <form onSubmit={handleProbe} className="relative">
          <div className="glass flex flex-col gap-2 rounded-2xl p-2 shadow-lift sm:flex-row sm:items-center sm:rounded-full sm:p-1.5">
            <div className="flex flex-1 items-center gap-2 px-3">
              <Icon path={P.link} className="size-5 shrink-0 text-brand-500" />
              <input
                value={url}
                onChange={(e) => handleUrlChange(e.target.value)}
                inputMode="url"
                autoComplete="off"
                spellCheck={false}
                aria-label="视频链接"
                placeholder="粘贴视频链接，支持 YouTube / B站 / 抖音 / 小红书…"
                className="h-11 w-full min-w-0 bg-transparent text-[15px] text-ink-900 outline-none placeholder:text-ink-500/70"
              />
              {url && (
                <button
                  type="button"
                  onClick={() => handleUrlChange("")}
                  aria-label="清空"
                  className="rounded-full p-1 text-ink-500 transition hover:bg-slate-100"
                >
                  <Icon path={P.x} className="size-4" />
                </button>
              )}
            </div>
            <Button
              type="submit"
              size="lg"
              loading={probing}
              className="w-full shrink-0 sm:w-auto sm:rounded-full"
            >
              {probing ? "解析中…" : "立即解析"}
              {!probing && <Icon path={P.bolt} className="size-4" fill />}
            </Button>
          </div>
        </form>

        {!hasWorkspace && (
          <div className="mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-xs text-ink-500">
            <span className="inline-flex items-center gap-1">
              <Icon path={P.shield} className="size-3.5" /> 无需注册
            </span>
            <span className="inline-flex items-center gap-1">
              <Icon path={P.bolt} className="size-3.5" /> 服务器直连加速
            </span>
            <span className="inline-flex items-center gap-1">
              <Icon path={P.phone} className="size-3.5" /> 手机可直接保存
            </span>
            <span className="inline-flex items-center gap-1">
              <Icon path={P.globe} className="size-3.5" /> 1800+ 平台
            </span>
          </div>
        )}

        {error && (
          <Alert className="mt-4 animate-rise" tone="error">
            <span className="inline-flex items-start gap-2">
              <Icon path={P.alert} className="mt-0.5 size-4 shrink-0" />
              <span>{error}</span>
            </span>
          </Alert>
        )}
      </div>

      {hasWorkspace && (
        <div className="mt-5 grid items-start gap-5 text-left lg:grid-cols-2">
          <div className="min-w-0">
            <Card className="animate-rise overflow-hidden text-left">
              <div className="flex gap-4 p-4 sm:p-5">
                {probe.thumbnail ? (
                  // Plain <img>: thumbnails come from arbitrary platform CDNs, and
                  // next/image would need every one allowlisted in next.config.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={probe.thumbnail}
                    alt=""
                    className="hidden h-[76px] w-[135px] shrink-0 rounded-lg bg-slate-100 object-cover sm:block"
                    loading="lazy"
                    // Some CDNs reject a foreign Referer; sending none is more reliable.
                    referrerPolicy="no-referrer"
                  />
                ) : null}
                <div className="min-w-0 flex-1 text-left">
                  <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug text-ink-900">
                    {probe.title}
                  </h3>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-500">
                    {probe.extractor && <Badge tone="brand">{probe.extractor}</Badge>}
                    {probe.uploader && (
                      <span className="inline-flex items-center gap-1">
                        <Icon path={P.users} className="size-3.5" />
                        <span className="max-w-[10rem] truncate">{probe.uploader}</span>
                      </span>
                    )}
                    {probe.duration ? (
                      <span className="inline-flex items-center gap-1">
                        <Icon path={P.clock} className="size-3.5" />
                        {formatDuration(probe.duration)}
                      </span>
                    ) : null}
                    {probe.is_live && <Badge tone="amber">直播中</Badge>}
                  </div>
                </div>
              </div>

              <div className="border-t border-slate-100 bg-slate-50/60 p-4 sm:p-5">
                <ModeTabs
                  mode={mode}
                  onChange={setMode}
                  hasSubtitles={probe.has_subtitles}
                  isPro={limits?.is_pro ?? false}
                />

                {mode === "video" && (
                  <FormatPicker
                    formats={probe.formats}
                    selected={height}
                    onSelect={setHeight}
                    maxHeight={maxHeight}
                    isPro={limits?.is_pro ?? false}
                    onUpgrade={onUpgrade}
                  />
                )}

                {mode === "audio" && (
                  <p className="mt-4 text-sm text-ink-500">
                    将提取音频并转换为 MP3（192kbps），适合做铃声、听课或音乐收藏。
                  </p>
                )}

                {mode === "subtitle" && (
                  <SubtitlePicker
                    probe={probe}
                    selected={subtitleLang}
                    onSelect={setSubtitleLang}
                  />
                )}

                <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:items-center">
                  <Button
                    size="lg"
                    onClick={() => handleStart(mode)}
                    loading={starting || busy}
                    disabled={mode === "subtitle" && !subtitleLang}
                    className="w-full sm:w-auto"
                  >
                    <Icon path={P.download} className="size-4" />
                    {busy ? STATUS_TEXT[job!.status] : "开始下载"}
                  </Button>
                  {mode === "video" && height && height > 1080 && (
                    <span className="text-xs text-ink-500">
                      超清文件较大，建议在 Wi-Fi 下载
                    </span>
                  )}
                </div>
              </div>
            </Card>

            {job && <JobProgress job={job} onRetry={() => handleStart(job.mode)} />}
          </div>

          {summarySlot ? <div className="min-w-0">{summarySlot}</div> : null}
        </div>
      )}
    </div>
  );
}

function ModeTabs({
  mode,
  onChange,
  hasSubtitles,
  isPro,
}: {
  mode: JobMode;
  onChange: (m: JobMode) => void;
  hasSubtitles: boolean;
  isPro: boolean;
}) {
  const tabs: { key: JobMode; label: string; icon: string; pro?: boolean; disabled?: boolean }[] = [
    { key: "video", label: "视频", icon: P.film },
    { key: "audio", label: "MP3 音频", icon: P.music, pro: !isPro },
    { key: "subtitle", label: "字幕", icon: P.captions, disabled: !hasSubtitles },
  ];
  return (
    <div
      role="tablist"
      aria-label="下载类型"
      className="flex gap-1.5 overflow-x-auto hide-scrollbar"
    >
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={mode === t.key}
          disabled={t.disabled}
          onClick={() => onChange(t.key)}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition",
            mode === t.key
              ? "bg-white text-brand-600 shadow-soft ring-1 ring-brand-100"
              : "text-ink-500 hover:bg-white/70 hover:text-ink-700",
            t.disabled && "cursor-not-allowed opacity-45 hover:bg-transparent",
          )}
        >
          <Icon path={t.icon} className="size-4" />
          {t.label}
          {t.pro && <Badge tone="gold">Pro</Badge>}
          {t.disabled && <span className="text-[11px]">（无）</span>}
        </button>
      ))}
    </div>
  );
}

function FormatPicker({
  formats,
  selected,
  onSelect,
  maxHeight,
  isPro,
  onUpgrade,
}: {
  formats: FormatOption[];
  selected: number | null;
  onSelect: (h: number) => void;
  maxHeight: number;
  isPro: boolean;
  onUpgrade: () => void;
}) {
  if (!formats.length) {
    return <p className="mt-4 text-sm text-ink-500">该视频未提供可选清晰度，将下载默认画质。</p>;
  }
  const anyLocked = formats.some((f) => f.height > maxHeight);

  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-ink-700">选择清晰度</span>
        {anyLocked && !isPro && (
          <button
            onClick={onUpgrade}
            className="text-xs font-medium text-gold-600 transition hover:text-gold-500"
          >
            解锁 4K 超清 →
          </button>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {formats.map((f) => {
          const locked = f.height > maxHeight;
          const active = selected === f.height && !locked;
          return (
            <button
              key={f.height}
              onClick={() => (locked ? onUpgrade() : onSelect(f.height))}
              aria-pressed={active}
              className={cn(
                "relative flex flex-col items-start gap-0.5 rounded-xl border p-3 text-left transition",
                active
                  ? "border-brand-400 bg-brand-50/70 ring-2 ring-brand-100"
                  : "border-slate-200 bg-white hover:border-brand-300",
                locked && "opacity-70",
              )}
            >
              <span className="flex w-full items-center justify-between">
                <span className="text-sm font-semibold text-ink-900">{f.label}</span>
                {locked ? (
                  <Badge tone="gold">Pro</Badge>
                ) : f.height >= 1080 ? (
                  <Badge tone="brand">高清</Badge>
                ) : null}
              </span>
              <span className="text-[11px] text-ink-500">
                {f.filesize ? `约 ${formatBytes(f.filesize)}` : "大小未知"}
              </span>
              {/* Surfaced because a 4K AV1 file may not play on an iPhone at all. */}
              {!f.compatible && (
                <span className="text-[11px] text-amber-600">
                  AV1 编码，部分手机不支持
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SubtitlePicker({
  probe,
  selected,
  onSelect,
}: {
  probe: ProbeResult;
  selected: string | null;
  onSelect: (lang: string) => void;
}) {
  const manual = probe.subtitles;
  const auto = probe.auto_subtitles.filter((l) => !manual.includes(l));
  const shown = [...manual, ...auto.slice(0, 12)];

  return (
    <div className="mt-4">
      <span className="mb-2 block text-xs font-medium text-ink-700">
        选择字幕语言
        {auto.length > 0 && (
          <span className="ml-1 font-normal text-ink-500">（含自动生成）</span>
        )}
      </span>
      <div className="flex flex-wrap gap-1.5">
        {shown.map((lang) => (
          <button
            key={lang}
            onClick={() => onSelect(lang)}
            aria-pressed={selected === lang}
            className={cn(
              "rounded-lg border px-2.5 py-1.5 text-xs font-medium transition",
              selected === lang
                ? "border-brand-400 bg-brand-50 text-brand-600"
                : "border-slate-200 bg-white text-ink-700 hover:border-brand-300",
            )}
          >
            {lang}
            {!manual.includes(lang) && (
              <span className="ml-1 text-[10px] text-ink-500">自动</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

function JobProgress({ job, onRetry }: { job: Job; onRetry: () => void }) {
  // Lazy initializer rather than an effect: this only renders client-side (the
  // parent is a client component behind user interaction), so there is no
  // hydration mismatch to guard against.
  const [ios] = React.useState(() => isIOS());

  const active = ["queued", "probing", "downloading", "merging", "converting"].includes(
    job.status,
  );
  const indeterminate = job.status !== "downloading" || !job.total_bytes;

  return (
    <Card className="animate-rise mt-4 p-4 text-left sm:p-5">
      <div className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center gap-2 text-sm font-medium text-ink-900">
          {active && <Spinner className="size-4 text-brand-500" />}
          {job.status === "done" && (
            <span className="grid size-5 place-items-center rounded-full bg-emerald-500 text-white">
              <Icon path={P.check} className="size-3.5" />
            </span>
          )}
          {(job.status === "error" || job.status === "expired") && (
            <Icon path={P.alert} className="size-4 text-rose-500" />
          )}
          {STATUS_TEXT[job.status] ?? job.status}
        </span>
        {job.status === "downloading" && job.total_bytes ? (
          <span className="text-xs tabular-nums text-ink-500">
            {job.percent.toFixed(0)}%
          </span>
        ) : null}
      </div>

      {active && (
        <>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
            <div
              className={cn(
                "relative h-full rounded-full bg-linear-to-r from-brand-cyan to-brand-600 transition-[width] duration-500",
                indeterminate && "w-1/3",
              )}
              style={indeterminate ? undefined : { width: `${job.percent}%` }}
            >
              <span className="absolute inset-0 shimmer-bar rounded-full" />
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-500">
            {job.total_bytes ? (
              <span className="tabular-nums">
                {formatBytes(job.downloaded_bytes)} / {formatBytes(job.total_bytes)}
              </span>
            ) : null}
            {job.speed ? <span className="tabular-nums">{formatSpeed(job.speed)}</span> : null}
            {job.eta ? <span>剩余 {formatEta(job.eta)}</span> : null}
            {job.status === "merging" && <span>高清视频与音频正在合并，请稍候</span>}
          </div>
        </>
      )}

      {job.status === "done" && (
        <div className="mt-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-ink-900">{job.filename}</p>
              <p className="text-xs text-ink-500">{formatBytes(job.filesize)}</p>
            </div>
            {/*
              Plain same-origin navigation to our own endpoint. Not a blob: mobile
              Safari has a low blob ceiling and tight tab memory, so buffering a
              video in JS would fail exactly where it matters.
            */}
            <a
              href={api.fileUrl(job.job_id)}
              download={job.filename ?? undefined}
              className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-linear-to-r from-brand-cyan via-brand-500 to-brand-600 px-5 text-[15px] font-medium text-white shadow-lift transition hover:brightness-110"
            >
              <Icon path={P.download} className="size-4" />
              保存到设备
            </a>
          </div>

          {ios && (
            <Alert tone="info" className="mt-3">
              <span className="inline-flex items-start gap-2">
                <Icon path={P.info} className="mt-0.5 size-4 shrink-0" />
                <span>
                  <strong className="font-medium">iPhone 保存提示：</strong>
                  点击上方按钮后，若视频直接在浏览器播放，请长按视频选择「下载链接文件」；
                  保存后可在「文件」App 的下载项中找到，再通过分享菜单选择「存储视频」即可存入相册。
                </span>
              </span>
            </Alert>
          )}
        </div>
      )}

      {(job.status === "error" || job.status === "expired") && (
        <div className="mt-3">
          <Alert tone="error">{job.error ?? "下载失败，请重试"}</Alert>
          <Button variant="outline" size="sm" onClick={onRetry} className="mt-3">
            重新尝试
          </Button>
        </div>
      )}
    </Card>
  );
}
