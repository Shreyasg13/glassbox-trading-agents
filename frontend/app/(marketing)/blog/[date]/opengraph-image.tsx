import { ImageResponse } from "next/og";
import { apiUrl } from "@/lib/api";
import { dayHeadline } from "@/lib/casebookText";
import type { CasebookPost } from "@/lib/types";
import { SHARE_SIZE, ShareCard } from "@/components/blog/ShareCard";

export const size = SHARE_SIZE;
export const contentType = "image/png";
export const alt = "GlassBox Research Committee Casebook";
export const revalidate = 3600;

/** The picture LinkedIn / X / Slack show when a post is shared, drawn from that day's real calls. */
export default async function Image({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  let post: CasebookPost | null = null;
  try {
    const r = await fetch(apiUrl(`/api/public/casebook/${date}`), { next: { revalidate: 3600 } });
    if (r.ok) post = await r.json();
  } catch {
    // fall through to the generic card
  }
  const headline = post ? dayHeadline(post.cases) : "Every AI committee call, public and scored.";
  return new ImageResponse(<ShareCard kicker={`Committee Casebook · ${date}`} headline={headline} cases={post?.cases ?? []} />, size);
}
