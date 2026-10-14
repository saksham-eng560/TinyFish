/** Public facts about the site, for the landing page, SEO and the footer. Override them per deployment. */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000").replace(/\/+$/, "");

/** Bump with every change to the icons in public/ (and the ?v= in manifest.webmanifest): it busts browsers' favicon caches. */
export const ICON_VERSION = "3";

export const GITHUB_URL = process.env.NEXT_PUBLIC_GITHUB_URL || "https://github.com/saksham-eng560/HireFlow";
/** The one-time Google OAuth client setup behind "Continue with Google". */
export const GOOGLE_SETUP_URL = `${GITHUB_URL}/blob/main/docs/USER_GUIDE.md#sign-in-with-google`;
export const AUTHOR = {
  name: process.env.NEXT_PUBLIC_AUTHOR_NAME || "saksham-eng560",
  url: process.env.NEXT_PUBLIC_AUTHOR_URL || "https://github.com/saksham-eng560",
};
/** The 60-second walkthrough: a YouTube or Loom link, or an .mp4/.webm file. Empty until one is recorded. */
export const DEMO_VIDEO_URL = process.env.NEXT_PUBLIC_DEMO_VIDEO_URL || "";

export const SITE_TITLE = "HireFlow — internship applications, on autopilot, with you in control";
export const SITE_DESCRIPTION =
  "HireFlow finds internships, lets you keep or skip each one with a swipe, then tailors your resume, fills the " +
  "application form and tracks replies — for the jobs you keep. Open source.";

/** Shared Open Graph tags (a page that sets its own openGraph replaces these, so it spreads them). */
export const OPEN_GRAPH = { type: "website", siteName: "HireFlow", title: SITE_TITLE, description: SITE_DESCRIPTION } as const;

export type VideoEmbed = { kind: "iframe"; src: string } | { kind: "file"; src: string } | null;

/** Turn a share link into something embeddable; unknown hosts aren't embedded (no surprise third-party frames). */
export function videoEmbed(url: string): VideoEmbed {
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return url.startsWith("/") && /\.(mp4|webm)$/i.test(url) ? { kind: "file", src: url } : null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  const host = parsed.hostname.replace(/^www\./, "");
  if (/\.(mp4|webm)$/i.test(parsed.pathname)) return { kind: "file", src: parsed.toString() };
  if (host === "youtu.be") {
    const id = parsed.pathname.slice(1).split("/")[0];
    return id ? { kind: "iframe", src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}` } : null;
  }
  if (host === "youtube.com" || host === "m.youtube.com" || host === "youtube-nocookie.com") {
    const id = parsed.searchParams.get("v") || parsed.pathname.match(/^\/(?:embed|shorts|live)\/([^/?]+)/)?.[1];
    return id ? { kind: "iframe", src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}` } : null;
  }
  if (host === "loom.com") {
    const id = parsed.pathname.match(/^\/(?:share|embed)\/([^/?]+)/)?.[1];
    return id ? { kind: "iframe", src: `https://www.loom.com/embed/${encodeURIComponent(id)}` } : null;
  }
  return null;
}
