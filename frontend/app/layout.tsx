import type { Metadata } from "next";
import { QueryProvider } from "@/lib/queryClient";
import { AuthProvider } from "@/lib/auth";
import { PageViewBeacon } from "@/components/analytics/PageViewBeacon";
import "./globals.css";

export const metadata: Metadata = {
  // Absolute base for share-image URLs (og:image); the site and the API share one public origin.
  metadataBase: new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3000"),
  title: "Glass Box",
  description: "Multi-agent trading system · glass UI",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,300..900;1,14..32,300..900&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
        {/* Sets data-theme before first paint so there's no flash of the
            wrong theme -- must run before CSS/hydration, so it's a blocking
            inline script, not a useEffect (which would run after paint). */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("glassbox-theme");if(t==="light"||t==="dark"){document.documentElement.dataset.theme=t;}else if(window.matchMedia("(prefers-color-scheme: light)").matches){document.documentElement.dataset.theme="light";}}catch(e){}})();`,
          }}
        />
      </head>
      <body>
        <AuthProvider>
          <PageViewBeacon />
          <QueryProvider>{children}</QueryProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
