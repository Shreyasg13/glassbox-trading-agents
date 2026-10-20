import type { Metadata } from "next";

export async function generateMetadata({ params }: { params: Promise<{ date: string }> }): Promise<Metadata> {
  const { date } = await params;
  const title = `What the AI committee argued on ${date} · GlassBox Research`;
  const description = "Every analyst's call and reasoning, the vote, and how it played out against SPY. Simulated research, not investment advice.";
  const images = [{ url: `/share/${date}`, width: 1200, height: 630, alt: `GlassBox Research Committee Casebook, ${date}` }];
  return { title, description, openGraph: { title, description, type: "article", images }, twitter: { card: "summary_large_image", title, description, images } };
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
