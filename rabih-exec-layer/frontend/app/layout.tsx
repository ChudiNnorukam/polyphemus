import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Execution Intelligence",
  description: "AI-native execution visibility from Slack",
};

const NAV = [
  { href: "/",        label: "Decisions" },
  { href: "/actions", label: "Actions"   },
  { href: "/risks",   label: "Risks"     },
  { href: "/drift",   label: "Drift"     },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen flex flex-col">
          <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center gap-8">
            <span className="font-semibold text-gray-900 text-sm tracking-tight">
              Execution Intelligence
            </span>
            <nav className="flex gap-1">
              {NAV.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="px-3 py-1.5 rounded text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
          </header>
          <main className="flex-1 px-6 py-8 max-w-7xl mx-auto w-full">{children}</main>
        </div>
      </body>
    </html>
  );
}
