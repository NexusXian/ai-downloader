"use client";

import * as React from "react";
import { ApiError, api, setToken, type Limits } from "@/lib/api";
import { Alert, Badge, Button, Card, Dialog } from "@/components/ui";
import { Icon, P } from "@/components/icons";
import { cn } from "@/lib/utils";

interface Tier {
  id: string;
  name: string;
  price: string;
  unit: string;
  note: string;
  cta: string;
  featured?: boolean;
  perks: string[];
  missing?: string[];
}

const TIERS: Tier[] = [
  {
    id: "free",
    name: "免费版",
    price: "¥0",
    unit: "永久免费",
    note: "适合偶尔下载一两个视频",
    cta: "当前版本",
    perks: ["720P 高清下载", "单个视频下载", "字幕下载", "1800+ 平台支持"],
    missing: ["4K 超清画质", "批量下载", "MP3 音频提取", "AI 总结与字幕翻译"],
  },
  {
    id: "pro",
    name: "Pro 会员",
    price: "¥19",
    unit: "/ 月",
    note: "学生党最常选的方案",
    cta: "立即升级 Pro",
    featured: true,
    perks: [
      "4K 超清原画下载",
      "一次批量 20 个链接",
      "MP3 音频提取",
      "AI 视频总结",
      "AI 字幕翻译",
      "下载队列优先处理",
    ],
  },
  {
    id: "year",
    name: "Pro 年付",
    price: "¥99",
    unit: "/ 年",
    note: "相当于每月 ¥8.25，省 ¥129",
    cta: "省钱之选",
    perks: [
      "包含全部 Pro 功能",
      "低至 4.3 折",
      "全年新功能免费用",
      "优先技术支持",
    ],
  },
];

export function Pricing({
  limits,
  onRedeemed,
  openSignal,
}: {
  limits: Limits | null;
  onRedeemed: () => void;
  openSignal: number;
}) {
  // Any upgrade affordance elsewhere on the page bumps openSignal. Tracking the
  // last-seen value derives "open" during render instead of syncing it in an
  // effect, which React 19 flags as a cascading render.
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [seenSignal, setSeenSignal] = React.useState(openSignal);

  if (openSignal !== seenSignal) {
    setSeenSignal(openSignal);
    if (openSignal > 0) setDialogOpen(true);
  }

  const isPro = limits?.is_pro ?? false;

  return (
    <section id="pricing" className="relative py-16 sm:py-24">
      <div className="orb -top-10 left-1/4 size-72 bg-brand-200/50" />
      <div className="orb bottom-0 right-1/4 size-64 bg-gold-300/40" />

      <div className="relative mx-auto max-w-6xl px-4">
        <div className="text-center">
          <Badge tone="gold">
            <Icon path={P.gift} className="size-3.5" />
            新用户首月立减 ¥10
          </Badge>
          <h2 className="mt-4 text-3xl font-bold tracking-tight text-ink-900 sm:text-4xl">
            选个方案，<span className="text-gradient-brand">把时间还给学习</span>
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-[15px] text-ink-500">
            一次批量下载省下的时间，比一杯奶茶更值。随时可停，无套路续费。
          </p>
        </div>

        <div className="mt-12 grid gap-5 lg:grid-cols-3">
          {TIERS.map((tier) => {
            const isCurrent =
              (tier.id === "free" && !isPro) || (tier.id !== "free" && isPro);
            return (
              <Card
                key={tier.id}
                className={cn(
                  "relative flex flex-col p-6 transition duration-300 hover:-translate-y-1",
                  tier.featured &&
                    "border-brand-300 shadow-lift ring-2 ring-brand-100 lg:-mt-3 lg:mb-3",
                )}
              >
                {tier.featured && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-linear-to-r from-brand-cyan to-brand-600 px-3 py-1 text-xs font-medium text-white shadow-lift">
                    最受欢迎
                  </span>
                )}

                <div className="flex items-center gap-2">
                  <h3 className="text-lg font-semibold text-ink-900">{tier.name}</h3>
                  {tier.id !== "free" && (
                    <Icon path={P.crown} className="size-4 text-gold-500" fill />
                  )}
                </div>
                <p className="mt-1 text-xs text-ink-500">{tier.note}</p>

                <div className="mt-5 flex items-end gap-1">
                  <span
                    className={cn(
                      "text-4xl font-bold tracking-tight",
                      tier.featured ? "text-gradient-brand" : "text-ink-900",
                    )}
                  >
                    {tier.price}
                  </span>
                  <span className="pb-1 text-sm text-ink-500">{tier.unit}</span>
                </div>

                <ul className="mt-6 flex-1 space-y-2.5">
                  {tier.perks.map((perk) => (
                    <li key={perk} className="flex items-start gap-2 text-sm text-ink-700">
                      <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-brand-50 text-brand-600">
                        <Icon path={P.check} className="size-3" />
                      </span>
                      {perk}
                    </li>
                  ))}
                  {tier.missing?.map((item) => (
                    <li
                      key={item}
                      className="flex items-start gap-2 text-sm text-ink-500/70"
                    >
                      <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-slate-100">
                        <Icon path={P.x} className="size-2.5" />
                      </span>
                      <span className="line-through decoration-slate-300">{item}</span>
                    </li>
                  ))}
                </ul>

                <Button
                  variant={
                    tier.id === "free" ? "outline" : tier.featured ? "primary" : "gold"
                  }
                  size="lg"
                  className="mt-6 w-full"
                  disabled={tier.id === "free"}
                  onClick={() => setDialogOpen(true)}
                >
                  {isCurrent && tier.id !== "free" ? "已是会员" : tier.cta}
                </Button>
              </Card>
            );
          })}
        </div>

        <p className="mt-8 text-center text-xs text-ink-500">
          支持支付宝 / 微信支付 · 开通后立即生效 · 有问题随时联系我们
        </p>
      </div>

      <RedeemDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onRedeemed={onRedeemed}
      />
    </section>
  );
}

function RedeemDialog({
  open,
  onClose,
  onRedeemed,
}: {
  open: boolean;
  onClose: () => void;
  onRedeemed: () => void;
}) {
  const [code, setCode] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [ok, setOk] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await api.redeem(code);
      setToken(result.token);
      setOk(true);
      onRedeemed();
      setTimeout(() => {
        setOk(false);
        onClose();
      }, 1400);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "兑换失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="升级 Pro 会员">
      {ok ? (
        <div className="py-6 text-center">
          <span className="mx-auto grid size-14 place-items-center rounded-full bg-emerald-500 text-white">
            <Icon path={P.check} className="size-7" />
          </span>
          <p className="mt-4 text-lg font-semibold text-ink-900">升级成功</p>
          <p className="mt-1 text-sm text-ink-500">
            4K 画质、批量下载与 AI 功能已全部解锁
          </p>
        </div>
      ) : (
        <>
          <div className="rounded-xl bg-linear-to-br from-brand-50 to-gold-300/20 p-4">
            <p className="flex items-center gap-2 text-sm font-medium text-ink-900">
              <Icon path={P.rocket} className="size-4 text-brand-500" />
              演示环境说明
            </p>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-700">
              本项目未接入真实支付网关。请使用兑换码体验 Pro 功能，服务端会校验兑换码并签发一枚带
              签名的有效期令牌，权限限制是真实生效的。
            </p>
            <p className="mt-2 font-mono text-xs text-brand-600">
              可用兑换码：VIDEO-PRO-2026 / STUDENT-VIP / KIRO-UNLOCK
            </p>
          </div>

          <form onSubmit={submit} className="mt-4">
            <label
              htmlFor="redeem-code"
              className="mb-1.5 block text-sm font-medium text-ink-700"
            >
              兑换码
            </label>
            <input
              id="redeem-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              placeholder="请输入兑换码"
              className="h-11 w-full rounded-xl border border-slate-200 px-3.5 font-mono text-sm uppercase text-ink-900 outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
            />
            {error && (
              <Alert tone="error" className="mt-3">
                {error}
              </Alert>
            )}
            <Button
              type="submit"
              variant="gold"
              size="lg"
              loading={busy}
              className="mt-4 w-full"
            >
              <Icon path={P.crown} className="size-4" fill />
              确认升级
            </Button>
          </form>
        </>
      )}
    </Dialog>
  );
}
