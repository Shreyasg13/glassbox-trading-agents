import { shareImage } from "@/lib/shareImage";

export const revalidate = 3600;

/** Stable URL for a day's share image (/share/2026-10-16): og:image points here, and the admin Studio offers it as a download. */
export async function GET(_req: Request, { params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return new Response("Not found", { status: 404 });
  const img = await shareImage(date);
  img.headers.set("Content-Disposition", `inline; filename="glassbox-casebook-${date}.png"`);
  return img;
}
