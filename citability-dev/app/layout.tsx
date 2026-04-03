import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import { Analytics } from '@vercel/analytics/next'
import './globals.css'

const _inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
const _jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: 'citability.dev — AI Visibility Auditing',
  description: 'Measure what Ahrefs and Semrush don\'t: whether AI systems like ChatGPT, Perplexity, and Claude can find you, recommend you, and cite you.',
  metadataBase: new URL('https://citability.dev'),
  alternates: {
    canonical: '/',
  },
  openGraph: {
    title: 'citability.dev — AI Visibility Auditing',
    description: 'Measure what Ahrefs and Semrush don\'t: whether AI systems can find you, recommend you, and cite you.',
    url: 'https://citability.dev',
    siteName: 'citability.dev',
    type: 'website',
    images: [{ url: '/opengraph-image', width: 1200, height: 630, alt: 'citability.dev - AI Visibility Auditing' }],
  },
  other: {
    'article:modified_time': new Date().toISOString().split('T')[0],
  },
  icons: {
    icon: '/icon',
    apple: '/apple-icon',
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              '@context': 'https://schema.org',
              '@graph': [
                {
                  '@type': 'WebSite',
                  name: 'citability.dev',
                  url: 'https://citability.dev',
                  description: 'AI Visibility Auditing. Measure whether AI systems can find you, recommend you, and cite you.',
                  dateModified: '2026-04-02',
                },
                {
                  '@type': 'Organization',
                  name: 'citability.dev',
                  url: 'https://citability.dev',
                  founder: {
                    '@type': 'Person',
                    name: 'Chudi Nnorukam',
                    url: 'https://chudi.dev',
                  },
                },
              ],
            }),
          }}
        />
        {children}
        <Analytics />
      </body>
    </html>
  )
}
