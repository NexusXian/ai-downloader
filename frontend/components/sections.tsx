"use client";

import * as React from "react";
import { Badge, Card } from "@/components/ui";
import { Icon, P } from "@/components/icons";
import { cn } from "@/lib/utils";

const PLATFORMS = [
  "YouTube", "哔哩哔哩", "抖音", "TikTok", "微博", "小红书",
  "X / Twitter", "Instagram", "Vimeo", "西瓜视频", "优酷", "腾讯视频",
];

export function PlatformStrip() {
  return (
    <div className="mt-10">
      <p className="text-center text-xs text-ink-500">已支持 1800+ 视频平台</p>
      <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
        {PLATFORMS.map((name) => (
          <span
            key={name}
            className="rounded-full bg-white/70 px-3 py-1 text-xs font-medium text-ink-700 ring-1 ring-slate-200/80 backdrop-blur transition hover:ring-brand-200"
          >
            {name}
          </span>
        ))}
        <span className="rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-600 ring-1 ring-brand-100">
          还有更多…
        </span>
      </div>
    </div>
  );
}

const FEATURES = [
  {
    icon: P.bolt,
    title: "服务器直连，速度不看运气",
    body: "由服务器代为下载再回传，绕开平台对浏览器的限速与拦截，还能处理需要合并的高清分片流。",
    tone: "brand",
  },
  {
    icon: P.layers,
    title: "批量粘贴，一次搞定整个合集",
    body: "整理课程、连续剧、素材库时最省时间的功能。粘贴多个链接后自动排队，逐个完成。",
    tone: "gold",
  },
  {
    icon: P.phone,
    title: "手机也能真的存下来",
    body: "针对 iOS Safari 做了保存引导。文件从本站同源下发，避免平台直链在手机上失效。",
    tone: "brand",
  },
  {
    icon: P.music,
    title: "视频转 MP3，随时当播客听",
    body: "只要音频？一键提取 192kbps MP3，通勤路上把网课听完。",
    tone: "brand",
  },
  {
    icon: P.captions,
    title: "字幕下载 + AI 翻译",
    body: "自动字幕也能下载，外语字幕可一键翻译成中文，时间轴完整保留。",
    tone: "gold",
  },
  {
    icon: P.sparkles,
    title: "AI 总结，先看重点再决定",
    body: "两小时的讲座，先花 10 秒读完核心要点，再决定值不值得看完。",
    tone: "gold",
  },
];

export function Features() {
  return (
    <section id="features" className="relative py-16 sm:py-20">
      <div className="mx-auto max-w-6xl px-4">
        <div className="text-center">
          <Badge tone="brand">
            <Icon path={P.rocket} className="size-3.5" />
            为什么选它
          </Badge>
          <h2 className="mt-4 text-3xl font-bold tracking-tight text-ink-900 sm:text-4xl">
            把下载这件小事，<span className="text-gradient-brand">做到顺手</span>
          </h2>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <Card
              key={f.title}
              className="group p-5 transition duration-300 hover:-translate-y-1 hover:shadow-lift"
            >
              <span
                className={cn(
                  "grid size-11 place-items-center rounded-xl transition group-hover:scale-110",
                  f.tone === "gold"
                    ? "bg-linear-to-br from-gold-400 to-gold-600 text-white"
                    : "bg-linear-to-br from-brand-cyan to-brand-600 text-white",
                )}
              >
                <Icon path={f.icon} className="size-5" />
              </span>
              <h3 className="mt-4 text-[15px] font-semibold text-ink-900">{f.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-500">{f.body}</p>
            </Card>
          ))}
        </div>
      </div>
    </section>
  );
}

const STEPS = [
  { n: "1", title: "粘贴链接", body: "复制视频地址，粘贴到上方输入框。" },
  { n: "2", title: "选择画质", body: "看到真实的清晰度和文件大小，按需选择。" },
  { n: "3", title: "保存到设备", body: "服务器处理完成，一键存到手机或电脑。" },
];

export function Steps() {
  return (
    <section className="relative py-12">
      <div className="mx-auto max-w-4xl px-4">
        <div className="grid gap-4 sm:grid-cols-3">
          {STEPS.map((s, i) => (
            <div key={s.n} className="relative">
              <div className="glass rounded-2xl p-5">
                <span className="grid size-9 place-items-center rounded-full bg-linear-to-br from-brand-cyan to-brand-600 text-sm font-semibold text-white">
                  {s.n}
                </span>
                <h3 className="mt-3 text-sm font-semibold text-ink-900">{s.title}</h3>
                <p className="mt-1 text-xs leading-relaxed text-ink-500">{s.body}</p>
              </div>
              {i < STEPS.length - 1 && (
                <span className="absolute -right-2.5 top-1/2 hidden -translate-y-1/2 text-brand-300 sm:block">
                  <Icon path="M5 12h14m0 0-5-5m5 5-5 5" className="size-5" />
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const FAQS = [
  {
    q: "下载的视频保存在哪里？",
    a: "电脑端会进入浏览器的默认下载目录。iPhone 上会保存到「文件」App 的下载项，如需存进相册，可在文件 App 中通过分享菜单选择「存储视频」。Android Chrome 会直接进入下载文件夹。",
  },
  {
    q: "为什么有些 4K 选项标注「部分手机不支持」？",
    a: "很多平台的 4K 只提供 AV1 编码，而不少手机（尤其是 iPhone）无法播放该编码。我们会如实标注，并优先为你选择兼容性更好的 H.264 编码，确保下载完能正常播放。",
  },
  {
    q: "支持哪些网站？会不会失效？",
    a: "基于开源项目 yt-dlp，支持 1800+ 站点。平台改版时个别站点可能短暂失效，更新到最新版本即可恢复。",
  },
  {
    q: "受版权保护（DRM）的视频能下载吗？",
    a: "不能。带 DRM 加密的内容（如部分付费影视平台）在技术与合规上都不支持，遇到这类链接会明确提示。",
  },
  {
    q: "免费版和 Pro 的区别是什么？",
    a: "免费版支持 720P 单个视频下载和字幕下载。Pro 解锁 4K 超清、一次 20 个链接的批量下载、MP3 提取，以及 AI 总结与字幕翻译。",
  },
  {
    q: "下载的文件会保存在你们服务器上吗？",
    a: "只在下载期间临时存放，完成后到期自动清理，默认 1 小时。我们不建立账号体系，也不保存你的下载记录。",
  },
];

export function FAQ() {
  const [open, setOpen] = React.useState<number | null>(0);
  return (
    <section id="faq" className="relative py-16 sm:py-20">
      <div className="mx-auto max-w-3xl px-4">
        <div className="text-center">
          <Badge tone="slate">常见问题</Badge>
          <h2 className="mt-4 text-3xl font-bold tracking-tight text-ink-900">
            你可能想问
          </h2>
        </div>
        <div className="mt-10 space-y-2.5">
          {FAQS.map((f, i) => (
            <Card key={f.q} className="overflow-hidden">
              <button
                onClick={() => setOpen(open === i ? null : i)}
                aria-expanded={open === i}
                className="flex w-full items-center justify-between gap-4 p-4 text-left transition hover:bg-slate-50/70"
              >
                <span className="text-sm font-medium text-ink-900">{f.q}</span>
                <span
                  className={cn(
                    "shrink-0 text-ink-500 transition-transform duration-300",
                    open === i && "rotate-45",
                  )}
                >
                  <Icon path={P.plus} className="size-4" />
                </span>
              </button>
              {open === i && (
                <p className="animate-rise border-t border-slate-100 px-4 py-3.5 text-sm leading-relaxed text-ink-500">
                  {f.a}
                </p>
              )}
            </Card>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="mt-8 border-t border-slate-200/70 bg-white/60 py-10 backdrop-blur">
      <div className="mx-auto max-w-6xl px-4">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <span className="flex items-center gap-2 text-sm font-semibold text-ink-900">
              <span className="grid size-7 place-items-center rounded-lg bg-linear-to-br from-brand-cyan to-brand-600 text-white">
                <Icon path={P.download} className="size-4" />
              </span>
              万能视频下载器
            </span>
            <p className="mt-2 max-w-md text-xs leading-relaxed text-ink-500">
              基于开源项目 yt-dlp 构建，支持 1800+ 平台。
            </p>
          </div>
          <nav className="flex gap-6 text-xs text-ink-500">
            <a href="#features" className="transition hover:text-brand-600">功能</a>
            <a href="#pricing" className="transition hover:text-brand-600">价格</a>
            <a href="#faq" className="transition hover:text-brand-600">帮助</a>
          </nav>
        </div>

        <div className="mt-8 rounded-xl bg-slate-50/80 p-4 ring-1 ring-slate-200/70">
          <p className="text-[11px] leading-relaxed text-ink-500">
            <strong className="font-medium text-ink-700">使用须知：</strong>
            本工具仅供下载你拥有权利或已获授权的内容，以及公共领域、合理使用范围内的素材，
            请仅用于个人学习与备份，勿用于商业传播。请遵守各平台服务条款与所在地区的版权法律，
            使用者需自行承担相应责任。受 DRM 保护的内容不在支持范围内。
          </p>
        </div>

        <p className="mt-6 text-center text-[11px] text-ink-500">
          © {new Date().getFullYear()} 万能视频下载器 · Built with yt-dlp
        </p>
      </div>
    </footer>
  );
}
