import type { Metadata } from "next";

export async function generateMetadata({ params }: { params: Promise<{ date: string }> }): Promise<Metadata> {
  const { date } = await params;
  const title = `What the AI committee argued on ${date} · GlassBox Research`;
  const description = "Every analyst's call and reasoning, the vote, and how it played out against SPY. Simulated research, not investment advice.";
  return { title, description, openGraph: { title, description, type: "article" }, twitter: { card: "summary_large_image", title, description } };
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
