export function MarketingFooter() {
  return (
    <footer className="border-t border-border px-sp6 py-sp8 md:px-sp10">
      <div className="mx-auto flex max-w-[1100px] flex-col gap-sp5">
        <div className="flex flex-wrap items-center justify-between gap-sp4">
          <div className="flex items-center gap-sp2">
            <div className="grid h-[22px] w-[22px] place-items-center rounded-r1 bg-gradient-to-br from-teal to-blue text-[9px] font-extrabold text-bg">
              GB
            </div>
            <span className="text-[13px] font-bold text-t2">
              glass<span className="text-teal">box</span>
            </span>
          </div>
          <div className="flex flex-wrap gap-sp5 text-[12.5px] text-t3">
            <a href="#" className="hover:text-t1">Privacy Policy</a>
            <a href="#" className="hover:text-t1">Terms of Service</a>
            <a href="#" className="hover:text-t1">Disclaimer</a>
            <a href="/blog" className="hover:text-t1">Blog</a>
            <a href="#" className="hover:text-t1">Contact</a>
          </div>
        </div>
        <p className="text-[11.5px] leading-relaxed text-t4">
          Glass Box is a free, non-commercial research preview: simulated paper trading on a fixed list of stocks and funds.
          It does not constitute investment advice. All risk scores are for informational purposes only.
        </p>
        <p className="text-[11.5px] leading-relaxed text-t4">
          GlassBox Research is an independent project, built on its author&apos;s own time and equipment, unpaid, and not
          affiliated with or endorsed by any employer.
        </p>
      </div>
    </footer>
  );
}
