"use client";

import * as React from "react";
import { api, clearToken, getToken, type Limits, type ProbeResult } from "@/lib/api";
import { Badge, Button } from "@/components/ui";
import { Icon, P } from "@/components/icons";
import { Downloader } from "@/components/downloader";
import { BatchPanel } from "@/components/batch-panel";
import { AIPanel } from "@/components/ai-panel";
import { Pricing } from "@/components/pricing";
import { FAQ, Features, Footer, PlatformStrip, Steps } from "@/components/sections";

export default function Home() {
  const [limits, setLimits] = React.useState<Limits | null>(null);
  const [probe, setProbe] = React.useState<ProbeResult | null>(null);
  const [playableJobId, setPlayableJobId] = React.useState<string | null>(null);
  const [upgradeSignal, setUpgradeSignal] = React.useState(0);
  const [tokenRenewalSignal, setTokenRenewalSignal] = React.useState(0);
  const [offline, setOffline] = React.useState(false);
  const [hasToken, setHasToken] = React.useState(false);

  // Reuse the previous object for the same URL: AI state is keyed by probe URL, so
  // an equal-but-new result would remount the panel and restart a running summary.
  const handleProbed = React.useCallback((next: ProbeResult | null) => {
    setProbe((current) =>
      current && next && current.url === next.url ? current : next,
    );
  }, []);

  const refreshLimits = React.useCallback(async () => {
    setHasToken(!!getToken());
    try {
      setLimits(await api.limits());
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  React.useEffect(() => {
    // Deferred to a microtask so the initial fetch isn't a synchronous setState
    // inside the effect body (React 19 flags that as a cascading render).
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (!cancelled) void refreshLimits();
    });
    return () => {
      cancelled = true;
    };
  }, [refreshLimits]);

  const openUpgrade = React.useCallback(() => {
    setUpgradeSignal((n) => n + 1);
    document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" });
  }, []);

  const handleRedeemed = React.useCallback(() => {
    setTokenRenewalSignal((signal) => signal + 1);
    void refreshLimits();
  }, [refreshLimits]);

  const isPro = limits?.is_pro ?? false;

  return (
    <>
      <Header
        isPro={isPro}
        hasToken={hasToken}
        onUpgrade={openUpgrade}
        onSignOut={() => {
          clearToken();
          void refreshLimits();
        }}
      />

      <main className="relative flex-1 overflow-hidden">
        {/* Blur ornaments -- the reference UI leans on these heavily. */}
        <div className="orb animate-float -top-24 left-[8%] size-80 bg-brand-300/45" />
        <div
          className="orb animate-float right-[6%] top-10 size-72 bg-brand-200/50"
          style={{ animationDelay: "2.5s" }}
        />
        <div
          className="orb animate-float left-[38%] top-40 size-64 bg-gold-300/35"
          style={{ animationDelay: "5s" }}
        />

        <section className="relative px-4 pb-4 pt-14 sm:pt-20">
          <div className="mx-auto max-w-6xl text-center">
            {/* Collapses once a video loads so the workspace fits one screen. */}
            <div className="mx-auto max-w-4xl">
              {!probe && (
                <div className="animate-rise flex justify-center">
                  <Badge tone="gold" className="px-3 py-1">
                    <Icon path={P.bolt} className="size-3.5" fill />
                    已支持 1800+ 平台 · 手机电脑都能存
                  </Badge>
                </div>
              )}

              <h1
                className={
                  probe
                    ? "text-2xl font-bold tracking-tight text-ink-900 sm:text-3xl"
                    : "animate-rise mt-6 text-4xl font-bold leading-[1.15] tracking-tight text-ink-900 sm:text-6xl"
                }
                style={probe ? undefined : { animationDelay: "60ms" }}
              >
                想存的视频，
                <br className="sm:hidden" />
                <span className="text-gradient-brand">一粘就下</span>
              </h1>

              {!probe && (
                <p
                  className="animate-rise mx-auto mt-5 max-w-2xl text-[15px] leading-relaxed text-ink-500 sm:text-lg"
                  style={{ animationDelay: "120ms" }}
                >
                  不用装软件，不用注册。粘贴链接就能保存高清原画、提取 MP3、下载字幕，
                  还能批量处理整个合集 —— 手机上也一样好用。
                </p>
              )}
            </div>

            <div className="animate-rise mt-9" style={{ animationDelay: "180ms" }}>
              <Downloader
                limits={limits}
                onUpgrade={openUpgrade}
                onProbed={handleProbed}
                onPlayableJob={setPlayableJobId}
                summarySlot={
                  <AIPanel
                    key={probe?.url ?? "no-probe"}
                    limits={limits}
                    probe={probe}
                    onUpgrade={openUpgrade}
                    tokenRenewalSignal={tokenRenewalSignal}
                    playableJobId={playableJobId}
                  />
                }
              />
            </div>

            {offline && (
              <p className="mt-4 text-xs text-rose-600">
                无法连接后端服务，请先启动 backend（默认 http://127.0.0.1:8000）
              </p>
            )}

            {/* Hidden once a video is loaded: the workspace needs the vertical space. */}
            {!probe && <PlatformStrip />}
          </div>
        </section>

        <Steps />

        <section className="relative px-4 py-8">
          <div className="mx-auto max-w-3xl">
            <BatchPanel limits={limits} onUpgrade={openUpgrade} />
          </div>
        </section>

        <Features />
        <Pricing
          limits={limits}
          onRedeemed={handleRedeemed}
          openSignal={upgradeSignal}
        />
        <FAQ />
      </main>

      <Footer />
    </>
  );
}

function Header({
  isPro,
  hasToken,
  onUpgrade,
  onSignOut,
}: {
  isPro: boolean;
  hasToken: boolean;
  onUpgrade: () => void;
  onSignOut: () => void;
}) {
  const [scrolled, setScrolled] = React.useState(false);

  React.useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`sticky top-0 z-40 transition-all duration-300 ${
        scrolled ? "glass shadow-soft" : "bg-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
        <a href="#" className="flex items-center gap-2">
          <span className="grid size-8 place-items-center rounded-lg bg-linear-to-br from-brand-cyan to-brand-600 text-white shadow-lift">
            <Icon path={P.download} className="size-4" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-ink-900">
            万能视频下载器
          </span>
        </a>

        <nav className="hidden items-center gap-7 text-sm text-ink-700 md:flex">
          <a href="#features" className="transition hover:text-brand-600">功能</a>
          <a href="#pricing" className="transition hover:text-brand-600">价格</a>
          <a href="#faq" className="transition hover:text-brand-600">帮助</a>
        </nav>

        <div className="flex items-center gap-2">
          {isPro ? (
            <>
              <Badge tone="gold" className="px-2.5 py-1">
                <Icon path={P.crown} className="size-3.5" fill />
                Pro 会员
              </Badge>
              {hasToken && (
                <button
                  onClick={onSignOut}
                  className="hidden text-xs text-ink-500 transition hover:text-ink-700 sm:block"
                >
                  退出
                </button>
              )}
            </>
          ) : (
            <Button variant="gold" size="sm" onClick={onUpgrade}>
              <Icon path={P.crown} className="size-3.5" fill />
              升级 Pro
            </Button>
          )}
        </div>
      </div>
    </header>
  );
}
