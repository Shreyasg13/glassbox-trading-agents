"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { ProviderHealthStrip } from "@/components/admin/ProviderHealthStrip";

// Sign-out lives in AppShell's top bar now (always visible, every page,
// every viewport) -- this layout used to have its own separate sign-out
// button here, which only happened to be reachable from admin pages
// since AppShell's sidebar (the other sign-out) is desktop-only.

const tabs = [
  { href: "/admin/agents", label: "Agents" },
  { href: "/admin/orchestrations", label: "Orchestrations" },
  { href: "/admin/paper", label: "Paper Trading" },
  { href: "/admin/reports", label: "Reports" },
  { href: "/admin/studio", label: "Studio" },
  { href: "/admin/observability", label: "Observability" },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { token, role, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && (!token || role !== "admin")) {
      router.replace("/login");
    }
  }, [loading, token, role, router]);

  if (loading || !token || role !== "admin") {
    return <p className="py-sp10 text-center text-[13px] text-t3">Checking access…</p>;
  }

  return (
    <div className="flex flex-col gap-sp5">
      <ProviderHealthStrip />
      <div className="border-b border-border pb-sp3">
        <nav className="flex flex-wrap gap-x-sp5 gap-y-sp2">
          {tabs.map((t) => (
            <Link
              key={t.href}
              href={t.href}
              className={`text-[13px] font-semibold ${
                pathname?.startsWith(t.href) ? "text-teal" : "text-t3 hover:text-t1"
              }`}
            >
              {t.label}
            </Link>
          ))}
        </nav>
      </div>
      {children}
    </div>
  );
}
