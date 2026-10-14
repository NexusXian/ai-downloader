"use client";

import ReactMarkdown, { type Components } from "react-markdown";

import { Icon, P } from "@/components/icons";
import { Badge, Button } from "@/components/ui";
import type { SummaryJob } from "@/lib/api";

const TRANSCRIPT_SOURCE_TEXT: Record<
  NonNullable<SummaryJob["transcript_source"]>,
  string
> = {
  manual_caption: "创作者字幕",
  automatic_caption: "自动字幕",
  local_whisper: "本地 Whisper",
};

const MARKDOWN_ELEMENTS = [
  "a",
  "blockquote",
  "br",
  "code",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "strong",
  "ul",
] as const;

const MARKDOWN_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="text-2xl font-bold leading-tight text-ink-900">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-7 text-xl font-semibold leading-snug text-ink-900">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-6 text-base font-semibold leading-snug text-ink-900">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-5 text-sm font-semibold leading-snug text-ink-900">{children}</h4>
  ),
  h5: ({ children }) => (
    <h5 className="mt-4 text-sm font-semibold text-ink-900">{children}</h5>
  ),
  h6: ({ children }) => (
    <h6 className="mt-4 text-xs font-semibold uppercase tracking-wide text-ink-700">
      {children}
    </h6>
  ),
  p: ({ children }) => (
    <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-ink-700">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-2 pl-6 text-sm leading-7 text-ink-700">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-2 pl-6 text-sm leading-7 text-ink-700">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="mt-4 border-l-2 border-brand-200 bg-brand-50/50 py-2 pl-4 pr-3 text-ink-600">
      {children}
    </blockquote>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-brand-600 underline decoration-brand-200 underline-offset-2 hover:text-brand-500"
    >
      {children}
    </a>
  ),
  pre: ({ children }) => (
    <pre className="mt-4 overflow-x-auto rounded-xl bg-slate-950 p-4 text-xs leading-6 text-slate-100">
      {children}
    </pre>
  ),
  code: ({ children }) => (
    <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.9em] text-ink-700">
      {children}
    </code>
  ),
  hr: () => <hr className="my-7 border-slate-200" />,
};

export function SummaryResultView({
  job,
  markdown,
  exporting,
  onExport,
}: {
  job: SummaryJob;
  markdown: string;
  exporting: boolean;
  onExport: () => void;
}) {
  return (
    <article className="animate-rise mt-5 overflow-hidden rounded-xl border border-brand-100 bg-white shadow-soft">
      <header className="flex flex-col gap-4 bg-linear-to-br from-brand-50/90 to-white p-4 sm:flex-row sm:items-start sm:justify-between sm:p-5">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brand-500">
            AI Markdown Summary
          </p>
          <h4 className="mt-1 text-base font-semibold leading-snug text-ink-900">
            {job.title}
          </h4>
          {(job.transcript_source || job.transcript_lang) && (
            <div className="mt-2 flex flex-wrap gap-2">
              {job.transcript_source && (
                <Badge tone="brand">
                  {TRANSCRIPT_SOURCE_TEXT[job.transcript_source]}
                </Badge>
              )}
              {job.transcript_lang && <Badge tone="slate">{job.transcript_lang}</Badge>}
            </div>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          loading={exporting}
          onClick={onExport}
          className="shrink-0"
        >
          <Icon path={P.download} className="size-4" />
          导出 Markdown
        </Button>
      </header>

      <div className="p-4 sm:p-5">
        <ReactMarkdown
          allowedElements={MARKDOWN_ELEMENTS}
          components={MARKDOWN_COMPONENTS}
          skipHtml
        >
          {markdown}
        </ReactMarkdown>
      </div>
    </article>
  );
}
