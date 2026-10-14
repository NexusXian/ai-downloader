function wholeSeconds(seconds: number): number {
  return Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
}

function matchesHost(hostname: string, domain: string): boolean {
  return hostname === domain || hostname.endsWith(`.${domain}`);
}

export function formatTimestamp(seconds: number): string {
  const total = wholeSeconds(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainingSeconds = total % 60;
  const paddedSeconds = String(remainingSeconds).padStart(2, "0");

  if (hours === 0) return `${minutes}:${paddedSeconds}`;
  return `${hours}:${String(minutes).padStart(2, "0")}:${paddedSeconds}`;
}

export function timestampUrl(sourceUrl: string, seconds: number): string | null {
  try {
    const url = new URL(sourceUrl);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;

    const timestamp = wholeSeconds(seconds);
    if (matchesHost(url.hostname, "youtube.com") || matchesHost(url.hostname, "youtu.be")) {
      url.searchParams.set("t", `${timestamp}s`);
    } else if (matchesHost(url.hostname, "bilibili.com")) {
      url.searchParams.set("t", String(timestamp));
    } else {
      return null;
    }

    return url.toString();
  } catch {
    return null;
  }
}
