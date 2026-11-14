import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy & Contact - Soren",
};

export default function PrivacyPage() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-800 bg-zinc-950">
        <div className="max-w-3xl mx-auto px-4 py-4 font-semibold tracking-tight">Soren</div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-8 space-y-6 text-sm leading-relaxed">
        <Link href="/" className="text-zinc-400 hover:text-white">
          &larr; Back to dashboard
        </Link>

        <div>
          <h1 className="text-xl font-semibold">Privacy &amp; Contact</h1>
          <p className="text-xs text-zinc-500 mt-1">Last updated 24 September 2026</p>
        </div>

        <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-5 space-y-2">
          <h2 className="font-semibold">Privacy</h2>
          <p className="text-zinc-400">
            Soren is a personal trading dashboard. There are no visitor accounts, forms or
            sign-ups, and nothing is collected about the people who view this page. There are no
            analytics, ads, cookies or tracking, and nothing is sold.
          </p>
          <p className="text-zinc-400">
            This site is hosted by <span className="text-zinc-200">Vercel</span>, which processes
            requests (including IP addresses) to deliver pages. Trading data, strategies and market
            data are held by the operator&apos;s own backend, not by this site.
          </p>
        </section>

        <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-5 space-y-2">
          <h2 className="font-semibold">Contact</h2>
          <p className="text-zinc-400">
            Questions or requests:{" "}
            <a href="https://github.com/simstre" target="_blank" rel="noopener noreferrer" className="text-emerald-400 hover:underline">
              github.com/simstre
            </a>
          </p>
        </section>

        <p className="text-xs text-zinc-600">Nothing on this site is financial advice.</p>
      </main>
    </div>
  );
}
