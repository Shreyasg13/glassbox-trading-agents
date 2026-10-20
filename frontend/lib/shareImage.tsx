import { ImageResponse } from "next/og";
import { apiUrl } from "@/lib/api";
import { dayHeadline } from "@/lib/casebookText";
import type { CasebookPost } from "@/lib/types";
import { SHARE_SIZE, ShareCard } from "@/components/blog/ShareCard";

/** The share picture for one casebook day, drawn from that day's real calls (used for og:image and the Studio's download). */
export async function shareImage(date: string): Promise<ImageResponse> {
  let post: CasebookPost | null = null;
  try {
    const r = await fetch(apiUrl(`/api/public/casebook/${date}`), { next: { revalidate: 3600 } });
    if (r.ok) post = await r.json();
  } catch {
    // fall through to the generic card
  }
  const headline = post ? dayHeadline(post.cases) : "Every AI committee call, public and scored.";
  return new ImageResponse(<ShareCard kicker={`Committee Casebook · ${date}`} headline={headline} cases={post?.cases ?? []} />, SHARE_SIZE);
}
