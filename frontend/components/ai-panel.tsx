"use client";

import * as React from "react";
import {
  ApiError,
  api,
  isUpgradeRequired,
  type Limits,
  type ProbeResult,
} from "@/lib/api";
import { Alert, Badge, Button, Card } from "@/components/ui";
import { Icon, P } from "@/components/icons";
import { cn } from "@/lib/utils";

/**
 * AI summary + subtitle translation.
 *
 * Key-optional: when the operator has not set ANTHROPIC_API_KEY the backend
 * reports ai_enabled=false and this renders as an unavailable preview rather than
 * throwing errors at the user.
 */
export function AIPanel({
  limits,
  probe,
  onUpgrade,
}: {
  limits: Limits | null;
  probe: ProbeResult | null;
  onUpgrade: () => void;
}) {
  const [tab, setTab] = React.useState<"summary" | "translate">("summary");
  const [busy, setBusy] = React.useState(false);
  const [summary, setSummary] = React.useState<string | null>(null);
  const [srt, setSrt] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);

  const enabled = limits?.ai_enabled ?? false;
  const isPro = limits?.is_pro ?? false;

  async function run() {
    if (!probe) {
      setError("请先在上方解析一个视频链接");
      return;
    }
    setError(null);
    setBusy(true);
    setSummary(null);
    setSrt(null);
    try {
      if (tab === "summary") {
        const result = await api.aiSummary(probe.url, probe.title);
        setSummary(result.summary);
      } else {
        const result = await api.aiTranslate(probe.url);
        setSrt(result.srt);
      }
    } catch (err) {
      if (isUpgradeRequired(err)) {
        onUpgrade();
        setError(err instanceof ApiError ? err.message : null);
      } else {
        setError(err instanceof ApiError ? err.message : "AI 处理失败");
      }
    } finally {
      setBusy(false);
    }
  }

  function copySrt() {
    if (!srt) return;
    void navigator.clipboard.writeText(srt).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  }

  return (
    <Card className="p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-ink-900">
            <Icon path={P.sparkles} className="size-5 text-brand-500" fill />
            AI 助手
            {!isPro && <Badge tone="gold">Pro</Badge>}
          </h3>
          <p className="mt-1 text-sm text-ink-500">
            一键生成视频总结，或把外语字幕翻译成中文。看长视频前先花 10 秒了解重点。
          </p>
        </div>
      </div>

      <div className="mt-4 flex gap-1.5">
        {(
          [
            { key: "summary", label: "视频总结", icon: P.sparkles },
            { key: "translate", label: "字幕翻译", icon: P.captions },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            aria-pressed={tab === t.key}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition",
              tab === t.key
                ? "bg-brand-50 text-brand-600 ring-1 ring-brand-100"
                : "text-ink-500 hover:bg-slate-50 hover:text-ink-700",
            )}
          >
            <Icon path={t.icon} className="size-4" />
            {t.label}
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
      ) : (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button onClick={run} loading={busy} disabled={!probe}>
              <Icon path={P.sparkles} className="size-4" fill />
              {tab === "summary" ? "生成总结" : "翻译成中文"}
            </Button>
            {!probe && (
              <span className="text-xs text-ink-500">请先解析一个视频链接</span>
            )}
            {probe && (
              <span className="max-w-[16rem] truncate text-xs text-ink-500">
                当前视频：{probe.title}
              </span>
            )}
          </div>

          {error && (
            <Alert tone="error" className="mt-3">
              {error}
            </Alert>
          )}

          {summary && (
            <div className="mt-4 rounded-xl bg-linear-to-br from-brand-50/80 to-white p-4 ring-1 ring-brand-100">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-700">
                {summary}
              </p>
            </div>
          )}

          {srt && (
            <div className="mt-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-ink-700">
                  翻译结果（SRT 格式，时间轴已保留）
                </span>
                <button
                  onClick={copySrt}
                  className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 transition hover:text-brand-500"
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
        </>
      )}
    </Card>
  );
}
