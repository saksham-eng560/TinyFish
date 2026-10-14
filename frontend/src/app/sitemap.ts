import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

/** The public pages; the dashboard is behind sign-in and isn't listed. */
export default function sitemap(): MetadataRoute.Sitemap {
  const pages: { path: string; priority: number; changeFrequency: "weekly" | "monthly" | "yearly" }[] = [
    { path: "", priority: 1, changeFrequency: "weekly" },
    { path: "/register", priority: 0.6, changeFrequency: "monthly" },
    { path: "/login", priority: 0.4, changeFrequency: "monthly" },
    { path: "/responsible-use", priority: 0.5, changeFrequency: "monthly" },
    { path: "/privacy", priority: 0.3, changeFrequency: "yearly" },
    { path: "/terms", priority: 0.3, changeFrequency: "yearly" },
  ];
  return pages.map((p) => ({ url: `${SITE_URL}${p.path || "/"}`, priority: p.priority, changeFrequency: p.changeFrequency }));
}
