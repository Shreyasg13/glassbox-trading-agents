"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NotificationBell } from "@/components/notifications/NotificationBell";

type NavItem = { href: string; label: string; icon: string };

const userLinks: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "◈" },
  { href: "/onboarding", label: "Onboarding", icon: "✦" },
  { href: "/notifications", label: "Inbox", icon: "✉" },
  { href: "/ask", label: "Assistant", icon: "◎" },
  { href: "/track-record", label: "Track record", icon: "◔" },
  { href: "/reports", label: "Reports", icon: "▤" },
  { href: "/agents", label: "My Agents", icon: "⚗" },
];

const adminLinks: NavItem[] = [
  { href: "/admin/strategy", label: "Strategy", icon: "✧" },
  { href: "/admin/traction", label: "Traction", icon: "↗" },
  { href: "/admin/studio", label: "Daily Studio", icon: "✎" },
  { href: "/admin/agents", label: "Agent Factory", icon: "⚗" },
  { href: "/admin/orchestrations", label: "Orchestrations", icon: "⟳" },
  { href: "/admin/paper", label: "Paper Trading", icon: "◔" },
  { href: "/admin/reports", label: "Report Builder", icon: "▤" },
  { href: "/admin/observability", label: "Observability", icon: "≡" },
];

// /reports and /reports/[id] are intentionally public — preserve that
// even though this shell otherwise gates the (app) route group.
function isPublicPath(pathname: string): boolean {
  return pathname === "/reports" || pathname.startsWith("/reports/");
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { token, role, loading, logout } = useAuth();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const isAdmin = !!token && role === "admin";

  useEffect(() => {
    if (!loading && !token && !isPublicPath(pathname)) {
      router.replace("/login");
    }
  }, [loading, token, pathname, router]);

  if (loading) {
    return <p className="py-sp10 text-center text-[13px] text-t3">Loading…</p>;
  }

  if (!token && !isPublicPath(pathname)) {
    return <p className="py-sp10 text-center text-[13px] text-t3">Redirecting to sign in…</p>;
  }

  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[230px_minmax(0,1fr)]">
      <aside className="hidden flex-col gap-sp1 border-r border-border bg-bg1/90 p-sp3 md:flex">
        <Link href="/" className="mb-sp6 flex items-center gap-sp3 px-sp3 py-sp2">
          <div className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-r2 bg-gradient-to-br from-teal to-blue text-[13px] font-extrabold text-bg shadow-teal">
            G
          </div>
          <div>
            <div className="text-[15px] font-extrabold tracking-tight text-t1">GlassBox</div>
            <div className="mono text-[9px] uppercase tracking-wide text-t3">live</div>
          </div>
        </Link>

        <div className="px-sp3 pb-sp2 text-[10px] font-bold uppercase tracking-wide text-t4">
          Overview
        </div>
        <nav className="flex flex-col gap-sp1">
          {userLinks.map((l) => (
            <SidebarLink key={l.href} item={l} active={pathname.startsWith(l.href)} />
          ))}
        </nav>

        {isAdmin && (
          <>
            <div className="mt-sp4 px-sp3 pb-sp2 text-[10px] font-bold uppercase tracking-wide text-t4">
              Admin
            </div>
            <nav className="flex flex-col gap-sp1">
              {adminLinks.map((l) => (
                <SidebarLink key={l.href} item={l} active={pathname.startsWith(l.href)} />
              ))}
            </nav>
          </>
        )}

        <div className="mt-auto pt-sp4">
          {token ? (
            <button
              onClick={() => {
                logout();
                router.push("/");
              }}
              className="flex w-full items-center gap-sp3 rounded-r2 border border-border bg-panel px-sp3 py-sp3 text-left transition-colors hover:border-border2"
            >
              <div className="grid h-[32px] w-[32px] shrink-0 place-items-center rounded-full bg-gradient-to-br from-blue to-teal text-[12px] font-bold text-bg">
                {(role ?? "?")[0].toUpperCase()}
              </div>
              <div>
                <div className="text-[12px] font-semibold text-t1">Sign Out</div>
                <div className="text-[10px] text-t3">{role}</div>
              </div>
            </button>
          ) : (
            <Link href="/login" className="btn btn-primary w-full justify-center">
              Sign In
            </Link>
          )}
        </div>
      </aside>

      <div className="flex flex-col">
        <div className="glass-nav sticky top-0 z-40 flex items-center gap-sp4 px-sp5 py-sp3">
          <span className="mono text-[11px] text-t3">GlassBox</span>
          <div className="flex-1" />
          <div className="flex items-center gap-sp2 rounded-r4 border border-green-dim bg-green-dim px-sp3 py-1 text-[11px] font-semibold text-green">
            <span className="h-[6px] w-[6px] animate-pulse rounded-full bg-green" />
            MARKET OPEN
          </div>
          <NotificationBell />
          <ThemeToggle />
          {/* Always visible regardless of viewport -- the sidebar's own
              sign-out (below) is desktop-only (md:flex), so this is the
              one sign-out control every page and every screen size can
              reach. See admin/layout.tsx, which used to have its own
              separate always-visible sign-out button -- removed in favor
              of this one so there's a single, global, non-duplicated
              control rather than one that only happened to be reachable
              from admin pages. */}
          {token && (
            <button
              onClick={() => {
                logout();
                router.push("/");
              }}
              className="btn btn-ghost text-[12px]"
            >
              Sign Out
            </button>
          )}
        </div>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-sp5 py-sp6">{children}</main>
      </div>
    </div>
  );
}

function SidebarLink({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      href={item.href}
      className={`relative flex items-center gap-sp3 rounded-r2 px-sp3 py-2 text-[13px] font-medium transition-all duration-150 ${
        active ? "bg-teal-dim font-semibold text-teal" : "text-t2 hover:translate-x-0.5 hover:bg-panel hover:text-t1"
      }`}
    >
      <span
        className={`absolute left-0 top-1/2 h-[16px] w-[3px] -translate-y-1/2 rounded-r1 bg-teal shadow-teal transition-opacity duration-150 ${
          active ? "opacity-100" : "opacity-0"
        }`}
      />
      <span className="w-[18px] text-center opacity-80">{item.icon}</span>
      {item.label}
    </Link>
  );
}
