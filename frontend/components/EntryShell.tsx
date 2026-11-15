// Shared frame for the two entry pages (join, create session), from the Figma "Join" frames:
// wordmark top-left, one centred card, a quiet link under it, and a three-part footer line.
import Link from "next/link";

export function EntryShell({
  children,
  below,
  step,
  motto,
}: {
  children: React.ReactNode;
  below: { href: string; label: string };
  step: string; // bottom-left, e.g. "01 / CONNECT"
  motto: string; // bottom-right
}) {
  return (
    <div className="mx-auto flex min-h-dvh max-w-[1440px] flex-col">
      <header className="flex items-center justify-between px-6 pt-7 sm:px-12 sm:pt-[34px]">
        <Link href="/" className="flex items-center gap-[18px] font-code text-[18px] font-medium">
          <span className="text-[30px] font-normal leading-none text-teal">↻</span>
          LectureLoop
        </Link>
        <p className="hidden font-code text-[12px] text-muted lg:block lg:pr-[168px]">
          A little more understanding. Every lecture.
        </p>
      </header>

      <main className="flex flex-1 flex-col items-center justify-center px-5 py-12">
        <section className="w-full max-w-[350px] rounded-[12px] border border-line bg-panel px-[23px] pb-9 pt-[29px] sm:max-w-[480px] sm:px-[39px] sm:pb-[66px]">
          {children}
        </section>
        <Link href={below.href} className="mt-8 font-code text-[12px] text-muted transition hover:text-fg sm:mt-[34px]">
          {below.label}
        </Link>
      </main>

      <footer className="grid grid-cols-1 items-end px-6 pb-[70px] font-code text-[11px] text-muted sm:px-12 lg:grid-cols-3 lg:pb-[60px]">
        <p className="hidden lg:block">{step}</p>
        <p className="text-center lg:pb-1">No account. No judgement. Just learning.</p>
        <p className="hidden text-right lg:block lg:pr-[52px]">{motto}</p>
      </footer>
    </div>
  );
}

/** Card typography shared by both entry pages. */
export function EntryHeading({ eyebrow, title, sub }: { eyebrow: string; title: [string, string]; sub: [string, string] }) {
  return (
    <>
      <p className="font-code text-[11px] text-teal">{eyebrow}</p>
      <h1 className="mt-[22px] text-[32px] font-medium leading-[1.21] sm:text-[38px]">
        {title[0]}
        <br />
        {title[1]}
      </h1>
      <p className="mt-[5px] text-[14px] leading-[1.21] text-muted">
        {sub[0]}
        <br />
        {sub[1]}
      </p>
    </>
  );
}

export const ENTRY_LABEL = "block font-code text-[11px] text-muted";
// 16px on phones stops iOS zooming into the field; the design's 12px applies from sm up.
export const ENTRY_INPUT =
  "mt-[10px] block h-12 w-full rounded-[8px] border border-line bg-ink px-[14px] font-code text-[16px] text-fg outline-none transition-colors placeholder:text-[12px] placeholder:text-muted focus:border-teal sm:text-[12px]";
export const ENTRY_BUTTON =
  "mt-[22px] h-12 w-full whitespace-pre rounded-[8px] bg-teal font-code text-[13px] font-bold text-ink transition hover:opacity-90 disabled:opacity-50";
export const ENTRY_ERROR = "mt-4 font-code text-[11px] text-coral";
