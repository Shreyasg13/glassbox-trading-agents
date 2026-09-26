"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ThemeToggle } from "@/components/ThemeToggle";

// Absolute "/#section" hrefs, not bare "#section" -- this nav also renders
// on /login and /signup (see app/login/page.tsx, app/signup/page.tsx), and
// a bare hash href only ever scrolls within the CURRENT page. From those
// pages a bare "#how-it-works" just appends the hash to /login with
// nothing there to scroll to. "/#how-it-works" + next/link's Link (not a
// plain <a>) navigates to the homepage first when needed, then scrolls --
// and still does an in-page scroll with no full reload when already on it.
const links = [
  { href: "/#how-it-works", label: "How it Works" },
  { href: "/#strategy-lenses", label: "Strategy Lenses" },
  { href: "/#access", label: "Free access" },
  { href: "/#blog", label: "Blog" },
];

export function MarketingNav() {
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    // hrefs are "/#id" -- strip the leading "/#" (2 chars), not just "#".
    const ids = links.map((l) => l.href.slice(2));
    const sections = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (sections.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting);
        if (visible.length > 0) {
          setActive(`/#${visible[0].target.id}`);
        }
      },
      { rootMargin: "-45% 0px -45% 0px", threshold: 0 }
    );

    sections.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return (
    <nav className="glass-nav sticky top-0 z-50 flex h-[100px] items-center gap-sp6 px-sp6 md:px-sp10">
      {/* Single brand anchor: logo-mark.png already draws the "GlassBox"
          wordmark itself (see the art), so there is deliberately no second
          <span>GlassBox</span> text next to it -- that was a real duplicate
          brand render, not a design choice. */}
      <Link href="/" className="flex shrink-0 items-center">
        {/* Overlay accents on top of the flattened logo-mark.png: "porthole"
            crops of the SAME image (see .logo-porthole in globals.css) sit
            exactly over the AAPL/NVDA/MSFT/TSLA fish, the rod, and the
            octopus tentacles and wiggle in place -- there's no separated
            layer art, so this fakes per-element motion without any seam
            since each porthole matches the base image pixel-for-pixel at
            rest. Dollar-sign particles are positioned on the $ marks
            already painted next to each fish. Coordinates are eyeballed
            percentages of the source art -- nudge if misaligned. */}
        {/* logo-mark.png is a full-bleed rounded-square icon with small
            dark triangles filling the canvas corners OUTSIDE its own
            rounded edge (normal for an app-icon asset). A square clip
            (no radius) left those triangles visible, reading as a hard
            square boundary around the rounded icon -- two boundaries.
            This radius (~19% of the box, matching the art's own corner
            curve) is a PERCENTAGE so it stays correct at any --logo-size,
            clipping the container right along the icon's rounded edge so
            only the artwork's single rounded border is visible.

            --logo-size drives both this box's rendered dimensions AND
            every .logo-porthole/.logo-water background-size/position
            (globals.css) from one place -- resize the logo here and the
            whole animation system (fish/rod/tentacle/water) rescales with
            it automatically. Smaller on mobile per "shrink the logo
            further rather than growing the header" rather than a fixed
            size that would force a taller bar on small screens. */}
        <div
          className="relative shrink-0 overflow-hidden rounded-[18.7%] [--logo-size:68px] w-[var(--logo-size)] h-[var(--logo-size)] md:[--logo-size:92px]"
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- fixed
              static brand asset (public/logo-mark.png), not user content;
              plain <img> avoids next/image's layout-shift reservation. */}
          <img
            src="/logo-mark.png"
            alt="GlassBox"
            className="h-full w-full object-contain"
          />

          {/* Water flowing left-to-right underneath everything else -- a
              horizontal band of the same image, tiled and scrolled. Sits
              behind the fish (rendered first, so later elements paint on
              top) so the fish appear to float on top of moving water
              rather than sliding sideways with it. Band is 60-68%: the
              narrow strip of plain wave crest between the fish row and
              the "GlassBox / See the data..." wordmark starting at ~71%
              -- it must stay clear of that text, which is what was
              blurring/appearing to move before. */}
          <span
            aria-hidden
            className="logo-water"
            style={{ left: 0, top: "60%", width: "100%", height: "8%", backgroundPositionY: "calc(var(--logo-size) * -0.60)" }}
          />

          {/* AAPL -- the one actually being caught: floats, then gets
              hooked and pulled up on the same story clock as the rod.
              backgroundPosition = calc(var(--logo-size) * -left%),
              calc(var(--logo-size) * -top%): each porthole's background
              must be offset by its own position, scaled to the current
              --logo-size, so the crop it shows lines up with the base
              image underneath at any rendered size. */}
          <span
            aria-hidden
            className="logo-porthole logo-fish-catch"
            style={{ left: "56%", top: "19%", width: "19%", height: "18%", backgroundPosition: "calc(var(--logo-size) * -0.56) calc(var(--logo-size) * -0.19)", transformOrigin: "50% 100%" }}
          />
          {/* NVDA / MSFT / TSLA -- just ambient water motion, unsynced */}
          <span aria-hidden className="logo-porthole logo-fish-b" style={{ left: "13%", top: "47%", width: "16%", height: "14%", backgroundPosition: "calc(var(--logo-size) * -0.13) calc(var(--logo-size) * -0.47)", animationDelay: "0.3s" }} />
          <span aria-hidden className="logo-porthole logo-fish-a" style={{ left: "40%", top: "53%", width: "16%", height: "14%", backgroundPosition: "calc(var(--logo-size) * -0.40) calc(var(--logo-size) * -0.53)", animationDelay: "0.7s" }} />
          <span aria-hidden className="logo-porthole logo-fish-b" style={{ left: "73%", top: "55%", width: "16%", height: "14%", backgroundPosition: "calc(var(--logo-size) * -0.73) calc(var(--logo-size) * -0.55)", animationDelay: "1.1s" }} />

          {/* Fishing rod -- pivots near the character's hands, dips to
              hook AAPL on the same 8s story clock */}
          <span
            aria-hidden
            className="logo-porthole logo-rod"
            style={{ left: "40%", top: "5%", width: "28%", height: "32%", backgroundPosition: "calc(var(--logo-size) * -0.40) calc(var(--logo-size) * -0.05)", transformOrigin: "18% 88%" }}
          />

          {/* Just the nearest tentacle (not the whole octopus body/head)
              reaches toward the catch, same clock; the rest of the octopus
              stays put in the static base image. */}
          <span
            aria-hidden
            className="logo-porthole logo-tentacles"
            style={{ left: "60%", top: "26%", width: "20%", height: "18%", backgroundPosition: "calc(var(--logo-size) * -0.60) calc(var(--logo-size) * -0.26)", transformOrigin: "85% 85%" }}
          />
          <span aria-hidden className="logo-risk-glow" style={{ top: "22%", right: "4%", width: "44%", height: "44%" }} />

          {/* Ambient $ sparkle on the wave fish; AAPL's own $ only fires
              at the moment it's actually hooked (logo-dollar-catch). */}
          <span aria-hidden className="logo-dollar-catch" style={{ left: "70%", top: "16%" }}>$</span>
          <span aria-hidden className="logo-dollar" style={{ left: "22%", top: "44%", animationDuration: "2.6s", animationDelay: "0.5s" }}>$</span>
          <span aria-hidden className="logo-dollar" style={{ left: "52%", top: "50%", animationDuration: "3.2s", animationDelay: "1s" }}>$</span>
          <span aria-hidden className="logo-dollar" style={{ left: "83%", top: "52%", animationDuration: "2.8s", animationDelay: "1.5s" }}>$</span>
        </div>
      </Link>

      <div className="hidden items-center gap-sp5 md:flex">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`relative text-[13px] font-medium transition-colors ${
              active === l.href ? "text-teal" : "text-t2 hover:text-t1"
            }`}
          >
            {l.label}
            <span
              className={`absolute -bottom-1 left-0 h-[2px] w-full origin-left rounded-full bg-teal transition-transform duration-200 ease-glass ${
                active === l.href ? "scale-x-100" : "scale-x-0"
              }`}
            />
          </Link>
        ))}
      </div>

      <div className="flex-1" />

      <div className="hidden items-center gap-sp2 rounded-r4 border border-teal/20 px-sp3 py-1 text-[11px] font-semibold text-teal sm:flex">
        <span className="live-dot" />
        Accuracy this week: 99.2%
      </div>

      <ThemeToggle />

      <Link href="/login" className="btn btn-ghost">
        Sign In
      </Link>
      <Link href="/login" className="btn btn-primary">
        Try Free · No card
      </Link>
    </nav>
  );
}
