/** Inline icons: a handful of paths beats shipping an icon font. */
export function Icon({
  path,
  className = "size-5",
  fill = false,
}: {
  path: string;
  className?: string;
  fill?: boolean;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill={fill ? "currentColor" : "none"}
      stroke={fill ? "none" : "currentColor"}
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={path} />
    </svg>
  );
}

export const P = {
  download: "M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2",
  link: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.8 1.8M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.8-1.8",
  bolt: "M13 2 4.1 12.5c-.6.7-.1 1.8.8 1.8H11l-1 7.7 8.9-10.5c.6-.7.1-1.8-.8-1.8H12l1-7.7Z",
  music: "M9 18V5l10-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm10-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  captions: "M3 5h18v14H3zM7 10h4M7 14h8M15 10h2",
  sparkles: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Zm7 9 .9 2.4L22 15l-2.1.6L19 18l-.9-2.4L16 15l2.1-.6L19 12Z",
  layers: "M12 2 2 7l10 5 10-5-10-5Zm0 9L2 16l10 5 10-5-10-5Z",
  check: "M20 6 9 17l-5-5",
  crown: "M3 18h18l-1.5-9-4.5 4L12 6l-3 7-4.5-4L3 18Z",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z",
  phone: "M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm3 18h4",
  globe: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0 0c2.8 0 5-4.5 5-10S14.8 2 12 2 7 6.5 7 12s2.2 10 5 10ZM2.5 9h19M2.5 15h19",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-14v5l3.5 2",
  film: "M3 4h18v16H3zM7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4",
  x: "M18 6 6 18M6 6l12 12",
  plus: "M12 5v14M5 12h14",
  copy: "M9 9h10v10a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V9Zm0-4h8a2 2 0 0 1 2 2M5 7v10a2 2 0 0 0 2 2",
  alert: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-14h.01M11 12h1v5h1",
  rocket: "M5 13c-1.5 1.5-2 5-2 5s3.5-.5 5-2m6.5-3.5a15 15 0 0 0 4-11 15 15 0 0 0-11 4L5 9l-2 1 4 1 3 3 1 4 1-2 1.5-2.5Z",
  users: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm14 10v-2a4 4 0 0 0-3-3.9M17 3.1a4 4 0 0 1 0 7.8",
  gift: "M20 12v9H4v-9M2 7h20v5H2zM12 22V7m0 0H7.5a2.5 2.5 0 1 1 0-5C11 2 12 7 12 7Zm0 0h4.5a2.5 2.5 0 1 0 0-5C13 2 12 7 12 7Z",
};
