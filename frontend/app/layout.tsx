import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "极速下载 · 万能视频下载器 | 一键保存全网视频",
  description:
    "支持 YouTube、B站、抖音、小红书等 1800+ 平台，高清无水印下载，支持批量下载、MP3 提取、字幕下载与 AI 总结。手机电脑都能用。",
  keywords: [
    "视频下载",
    "youtube下载",
    "b站视频下载",
    "抖音无水印",
    "批量下载视频",
    "视频转mp3",
    "字幕下载",
  ],
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  themeColor: "#1478ff",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // data-scroll-behavior="smooth" is required as of Next 16: it no longer overrides
  // scroll-behavior during navigation unless this attribute is present.
  return (
    <html lang="zh-CN" data-scroll-behavior="smooth" className="h-full">
      <body className="flex min-h-full flex-col antialiased">{children}</body>
    </html>
  );
}
