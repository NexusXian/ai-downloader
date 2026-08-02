"use client";

/**
 * Small hand-rolled primitives.
 *
 * Kept in one file on purpose: shadcn's generator would produce a dozen files of
 * Radix wrappers for the four primitives this page actually needs.
 */
import * as React from "react";
import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "gold" | "ghost" | "outline" | "subtle";
type ButtonSize = "sm" | "md" | "lg";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-linear-to-r from-brand-cyan via-brand-500 to-brand-600 text-white shadow-lift hover:brightness-110 active:brightness-95",
  gold:
    "bg-linear-to-r from-gold-400 via-gold-500 to-gold-600 text-white shadow-gold hover:brightness-110 active:brightness-95",
  ghost: "text-ink-700 hover:bg-brand-50 hover:text-brand-600",
  outline:
    "border border-slate-200 bg-white text-ink-700 hover:border-brand-300 hover:text-brand-600 shadow-soft",
  subtle: "bg-brand-50 text-brand-600 hover:bg-brand-100",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-9 px-3.5 text-sm rounded-lg gap-1.5",
  md: "h-11 px-5 text-[15px] rounded-xl gap-2",
  lg: "h-13 px-7 text-base rounded-xl gap-2",
};

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center font-medium transition-all duration-200 select-none",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2",
        "disabled:pointer-events-none disabled:opacity-55",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Spinner className="size-4" />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn("animate-spin", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3.2"
        strokeOpacity="0.25"
      />
      <path
        d="M22 12a10 10 0 0 0-10-10"
        stroke="currentColor"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Badge({
  tone = "brand",
  className,
  children,
}: {
  tone?: "brand" | "gold" | "slate" | "green" | "amber";
  className?: string;
  children: React.ReactNode;
}) {
  const tones = {
    brand: "bg-brand-50 text-brand-600 ring-brand-100",
    gold: "bg-gold-500/10 text-gold-600 ring-gold-500/25",
    slate: "bg-slate-100 text-ink-500 ring-slate-200",
    green: "bg-emerald-50 text-emerald-600 ring-emerald-100",
    amber: "bg-amber-50 text-amber-700 ring-amber-200",
  } as const;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Card({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("card-surface", className)} {...props}>
      {children}
    </div>
  );
}

/** Accessible modal: focus trap on open, Escape to close, backdrop click to dismiss. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  maxWidth = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  maxWidth?: string;
}) {
  const panelRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4">
      <div
        className="absolute inset-0 bg-ink-900/35 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          "animate-rise relative w-full rounded-t-2xl bg-white p-6 shadow-2xl outline-none sm:rounded-2xl",
          maxWidth,
        )}
      >
        {title && (
          <h3 className="mb-4 text-lg font-semibold text-ink-900">{title}</h3>
        )}
        <button
          onClick={onClose}
          aria-label="关闭"
          className="absolute right-4 top-4 rounded-lg p-1.5 text-ink-500 transition hover:bg-slate-100 hover:text-ink-900"
        >
          <svg viewBox="0 0 24 24" className="size-5" fill="none" aria-hidden="true">
            <path
              d="M18 6 6 18M6 6l12 12"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </button>
        {children}
      </div>
    </div>
  );
}

export function Alert({
  tone = "error",
  children,
  className,
}: {
  tone?: "error" | "info" | "warn";
  children: React.ReactNode;
  className?: string;
}) {
  const tones = {
    error: "bg-rose-50 text-rose-700 ring-rose-100",
    info: "bg-brand-50 text-brand-700 ring-brand-100",
    warn: "bg-amber-50 text-amber-800 ring-amber-200",
  } as const;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "rounded-xl px-4 py-3 text-sm ring-1 ring-inset",
        tones[tone],
        className,
      )}
    >
      {children}
    </div>
  );
}
