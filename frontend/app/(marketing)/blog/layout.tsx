import type { Metadata } from "next";

const title = "The Committee Casebook · GlassBox Research";
const description = "Seven AI analysts and three quant models argue over 15 stocks every trading day. Every call public, scored against SPY. Simulated research, not investment advice.";

export const metadata: Metadata = { title, description, openGraph: { title, description }, twitter: { card: "summary_large_image", title, description } };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
