"use client";

import * as React from "react";

import { Icon, P } from "@/components/icons";
import { api } from "@/lib/api";

export interface PlayerHandle {
  seek: (seconds: number) => void;
}

/**
 * Minimal player for a completed download, used as the mind map's seek target.
 *
 * Streams from the backend's inline endpoint, which serves byte ranges, so
 * seeking works without downloading the whole file first.
 */
export const VideoPlayer = React.forwardRef<
  PlayerHandle,
  { jobId: string; title: string | null }
>(function VideoPlayer({ jobId, title }, ref) {
  const videoRef = React.useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = React.useState(false);

  React.useImperativeHandle(ref, () => ({
    seek(seconds: number) {
      const video = videoRef.current;
      if (!video) return;
      video.currentTime = Math.max(0, seconds);
      void video.play().catch(() => {
        /* autoplay can be blocked; the seek still landed */
      });
      video.scrollIntoView({ behavior: "smooth", block: "nearest" });
    },
  }));

  React.useEffect(() => {
    setFailed(false);
  }, [jobId]);

  if (failed) {
    return (
      <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs text-ink-500 ring-1 ring-slate-200">
        视频文件已被清理或无法播放，请重新下载后再使用跳转。
      </p>
    );
  }

  return (
    <div className="mt-3">
      <video
        ref={videoRef}
        src={api.playUrl(jobId)}
        controls
        preload="metadata"
        playsInline
        onError={() => setFailed(true)}
        className="aspect-video w-full rounded-xl bg-black ring-1 ring-slate-200"
      >
        <track kind="captions" />
      </video>
      <p className="mt-2 flex items-center gap-1.5 text-xs text-ink-500">
        <Icon path={P.play} className="size-3.5 shrink-0" fill />
        <span className="truncate">
          {title ? `正在播放：${title}` : "点击导图节点可跳到对应时间点"}
        </span>
      </p>
    </div>
  );
});
