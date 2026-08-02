"use client";

import * as React from "react";
import {
  ApiError,
  api,
  formatBytes,
  isUpgradeRequired,
  pollJob,
  type Job,
  type Limits,
} from "@/lib/api";
import { Alert, Badge, Button, Card, Spinner } from "@/components/ui";
import { Icon, P } from "@/components/icons";
import { cn } from "@/lib/utils";

const STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  probing: "解析中",
  downloading: "下载中",
  merging: "合并中",
  converting: "转换中",
  done: "已完成",
  error: "失败",
  expired: "已过期",
};

export function BatchPanel({
  limits,
  onUpgrade,
}: {
  limits: Limits | null;
  onUpgrade: () => void;
}) {
  const [text, setText] = React.useState("");
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [rejected, setRejected] = React.useState<{ url: string; error: string }[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const stops = React.useRef<(() => void)[]>([]);

  React.useEffect(
    () => () => {
      stops.current.forEach((fn) => fn());
    },
    [],
  );

  const urls = React.useMemo(
    () =>
      text
        .split(/[\n,\s]+/)
        .map((s) => s.trim())
        .filter((s) => s.length > 6),
    [text],
  );

  const cap = limits?.batch_limit ?? 1;
  const isPro = limits?.is_pro ?? false;
  const overCap = urls.length > cap;

  async function handleStart() {
    setError(null);
    setRejected([]);
    if (!urls.length) {
      setError("请至少粘贴一个视频链接");
      return;
    }
    if (overCap) {
      onUpgrade();
      return;
    }

    setBusy(true);
    stops.current.forEach((fn) => fn());
    stops.current = [];
    try {
      const result = await api.batch({ urls });
      setJobs(result.jobs);
      setRejected(result.rejected);
      stops.current = result.jobs.map((job) =>
        pollJob(job.job_id, (updated) =>
          setJobs((prev) =>
            prev.map((j) => (j.job_id === updated.job_id ? updated : j)),
          ),
        ),
      );
    } catch (err) {
      if (isUpgradeRequired(err)) {
        onUpgrade();
        setError(err instanceof ApiError ? err.message : null);
      } else {
        setError(err instanceof ApiError ? err.message : "批量下载启动失败");
      }
    } finally {
      setBusy(false);
    }
  }

  const doneCount = jobs.filter((j) => j.status === "done").length;

  return (
    <Card className="p-5 sm:p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-ink-900">
            <Icon path={P.layers} className="size-5 text-brand-500" />
            批量下载
            {!isPro && <Badge tone="gold">Pro</Badge>}
          </h3>
          <p className="mt-1 text-sm text-ink-500">
            一次粘贴多个链接，排队自动下载。整理课程、合集、素材时省下大量时间。
          </p>
        </div>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
        spellCheck={false}
        aria-label="批量视频链接"
        placeholder={"每行一个链接，例如：\nhttps://www.bilibili.com/video/BV...\nhttps://www.youtube.com/watch?v=..."}
        className="mt-4 w-full resize-y rounded-xl border border-slate-200 bg-white p-3 text-sm text-ink-900 outline-none transition placeholder:text-ink-500/60 focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
      />

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <span
          className={cn(
            "text-xs",
            overCap ? "font-medium text-gold-600" : "text-ink-500",
          )}
        >
          已识别 {urls.length} 个链接
          {overCap
            ? ` · 免费版单次最多 ${cap} 个，升级后可批量 ${limits?.pro_batch_limit ?? 20} 个`
            : ` · 本次上限 ${cap} 个`}
        </span>
        <Button
          onClick={handleStart}
          loading={busy}
          variant={overCap ? "gold" : "primary"}
        >
          {overCap ? (
            <>
              <Icon path={P.crown} className="size-4" fill />
              升级解锁批量
            </>
          ) : (
            <>
              <Icon path={P.download} className="size-4" />
              开始批量下载
            </>
          )}
        </Button>
      </div>

      {error && (
        <Alert tone="error" className="mt-3">
          {error}
        </Alert>
      )}

      {rejected.length > 0 && (
        <Alert tone="warn" className="mt-3">
          {rejected.length} 个链接被跳过：{rejected[0].error}
        </Alert>
      )}

      {jobs.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between text-xs text-ink-500">
            <span>
              进度 {doneCount}/{jobs.length}
            </span>
            {doneCount === jobs.length && (
              <span className="font-medium text-emerald-600">全部完成</span>
            )}
          </div>
          <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200">
            {jobs.map((job) => (
              <li
                key={job.job_id}
                className="flex items-center gap-3 bg-white px-3 py-2.5"
              >
                <span className="shrink-0">
                  {job.status === "done" ? (
                    <span className="grid size-5 place-items-center rounded-full bg-emerald-500 text-white">
                      <Icon path={P.check} className="size-3" />
                    </span>
                  ) : job.status === "error" || job.status === "expired" ? (
                    <Icon path={P.alert} className="size-5 text-rose-500" />
                  ) : (
                    <Spinner className="size-4 text-brand-500" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-ink-900">
                    {job.title ?? job.filename ?? "解析中…"}
                  </span>
                  <span className="block text-[11px] text-ink-500">
                    {STATUS_TEXT[job.status]}
                    {job.status === "downloading" && job.total_bytes
                      ? ` · ${job.percent.toFixed(0)}%`
                      : ""}
                    {job.status === "done" && job.filesize
                      ? ` · ${formatBytes(job.filesize)}`
                      : ""}
                    {job.error ? ` · ${job.error}` : ""}
                  </span>
                </span>
                {job.status === "done" && (
                  <a
                    href={api.fileUrl(job.job_id)}
                    download={job.filename ?? undefined}
                    className="shrink-0 rounded-lg bg-brand-50 px-3 py-1.5 text-xs font-medium text-brand-600 transition hover:bg-brand-100"
                  >
                    保存
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
