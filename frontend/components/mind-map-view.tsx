"use client";

import * as React from "react";
import { Markmap } from "markmap-view";

import { Icon, P } from "@/components/icons";
import { Button } from "@/components/ui";
import type { MindMap } from "@/lib/api";
import { collectTimestamps, seekTimeForElement, toMarkmapTree } from "@/lib/mind-map";

export function MindMapCanvas({
  mindMap,
  onSeek,
}: {
  mindMap: MindMap;
  onSeek: ((seconds: number) => void) | null;
}) {
  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const markmapRef = React.useRef<Markmap | null>(null);

  const timestamps = React.useMemo(
    () => collectTimestamps(mindMap.root),
    [mindMap],
  );
  const tree = React.useMemo(() => toMarkmapTree(mindMap.root), [mindMap]);

  React.useEffect(() => {
    if (!svgRef.current) return;
    const instance = Markmap.create(svgRef.current, {
      autoFit: true,
      duration: 200,
      initialExpandLevel: 2,
      maxWidth: 260,
      paddingX: 12,
      spacingHorizontal: 72,
      spacingVertical: 8,
    });
    markmapRef.current = instance;
    return () => {
      markmapRef.current = null;
      instance.destroy();
    };
  }, []);

  React.useEffect(() => {
    void markmapRef.current?.setData(tree);
    void markmapRef.current?.fit();
  }, [tree]);

  // Delegated: markmap owns the SVG contents and re-renders them on fold/zoom, so
  // per-node listeners would be lost. Reading the id from the DOM keeps the click
  // handler independent of markmap's internal node identity.
  const handleClick = React.useEffectEvent((event: React.MouseEvent<SVGSVGElement>) => {
    if (!onSeek) return;
    const seconds = seekTimeForElement(event.target as Element | null, timestamps);
    if (seconds === null) return;
    event.stopPropagation();
    onSeek(seconds);
  });

  return (
    <div className="relative">
      <div className="absolute right-2 top-2 z-10">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void markmapRef.current?.fit()}
        >
          <Icon path={P.fit} className="size-4" />
          适应画布
        </Button>
      </div>
      <svg
        ref={svgRef}
        onClick={handleClick}
        role="img"
        aria-label={`思维导图：${mindMap.root.label}，共 ${mindMap.node_count} 个节点`}
        className="mind-map-canvas h-[26rem] w-full touch-pan-y rounded-xl bg-slate-50 ring-1 ring-slate-200 sm:h-[32rem]"
      />
    </div>
  );
}
