import { ImageResponse } from "next/og";
import { apiUrl } from "@/lib/api";
import { dayHeadline } from "@/lib/casebookText";
import type { CasebookFeed, CasebookPost } from "@/lib/types";
import { SHARE_SIZE, ShareCard } from "@/components/blog/ShareCard";

export const size = SHARE_SIZE;
export const contentType = "image/png";
export const alt = "GlassBox Research: the Committee Casebook";
export const revalidate = 3600;

/** Share image for /blog: the latest day's room. */
export default async function Image() {
  let post: CasebookPost | null = null;
  try {
    const feed: CasebookFeed = await (await fetch(apiUrl("/api/public/casebook/feed"), { next: { revalidate: 3600 } })).json();
    if (feed.latest) post = await (await fetch(apiUrl(`/api/public/casebook/${feed.latest}`), { next: { revalidate: 3600 } })).json();
  } catch {
    // generic card
  }
  const headline = post ? dayHeadline(post.cases) : "Every AI committee call, public and scored.";
  return new ImageResponse(<ShareCard kicker="The Committee Casebook" headline={headline} cases={post?.cases ?? []} />, size);
}
