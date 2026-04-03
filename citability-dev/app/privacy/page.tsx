import type { Metadata } from "next"
import { Mail } from "lucide-react"

export const metadata: Metadata = {
  title: "Privacy Policy - citability.dev",
  description:
    "Privacy policy for citability.dev. We explain what data we collect, how we use it, and your rights.",
}

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <header className="border-b border-border bg-background/95 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-4xl mx-auto px-4 h-16 flex items-center justify-between">
          <a href="/" className="font-mono text-sm font-bold text-foreground hover:text-teal transition-colors">
            citability<span className="text-teal">.dev</span>
          </a>
          <a
            href="/assess"
            className="bg-teal text-primary-foreground hover:opacity-90 font-semibold font-mono text-xs px-4 py-2 rounded-md transition-opacity"
          >
            Free Scan
          </a>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-16">
        {/* Header */}
        <div className="mb-12">
          <span className="font-mono text-xs text-teal tracking-widest uppercase">Privacy Policy</span>
          <h1 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground">
            Your Privacy Matters
          </h1>
          <p className="mt-4 text-sm text-muted-foreground">
            Last updated: April 3, 2026
          </p>
        </div>

        {/* Content */}
        <div className="prose prose-invert max-w-none text-sm text-foreground/90 space-y-8">
          {/* Introduction */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Introduction</h2>
            <p className="leading-relaxed">
              citability.dev ("we," "our," "us," or "Service") is committed to protecting your privacy. This Privacy Policy explains what information we collect, how we use it, and what rights you have.
            </p>
            <p className="leading-relaxed mt-3">
              By accessing and using citability.dev, you acknowledge that you have read, understood, and agree to be bound by all the provisions of this Privacy Policy.
            </p>
          </section>

          {/* Information We Collect */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Information We Collect</h2>

            <div className="space-y-4">
              <div>
                <h3 className="font-mono text-xs text-teal tracking-widest uppercase mb-2">Information You Provide Directly</h3>
                <p className="leading-relaxed">
                  When you use the free scan feature on citability.dev, we collect the following information:
                </p>
                <ul className="list-disc list-inside mt-2 space-y-1 text-foreground/80">
                  <li><strong>Name:</strong> Your full name or business name</li>
                  <li><strong>Email address:</strong> For scan results and optional follow-up communication</li>
                  <li><strong>Website URL:</strong> The website you want to scan</li>
                  <li><strong>Phone number (optional):</strong> For optional contact purposes only</li>
                </ul>
              </div>

              <div>
                <h3 className="font-mono text-xs text-teal tracking-widest uppercase mb-2">Information Collected Automatically</h3>
                <p className="leading-relaxed">
                  When you visit citability.dev, we automatically collect certain information:
                </p>
                <ul className="list-disc list-inside mt-2 space-y-1 text-foreground/80">
                  <li><strong>Analytics data:</strong> Through Vercel Analytics, we collect page views, referrer sources, and aggregate user behavior (anonymized). This helps us understand how the site is used and improve the user experience.</li>
                  <li><strong>IP address:</strong> Your IP address is logged by standard web server infrastructure</li>
                  <li><strong>Browser and device information:</strong> User agent, browser type, device type (desktop/mobile)</li>
                  <li><strong>Cookies:</strong> We use minimal essential cookies for site functionality and analytics</li>
                </ul>
              </div>

              <div>
                <h3 className="font-mono text-xs text-teal tracking-widest uppercase mb-2">Scan Data</h3>
                <p className="leading-relaxed">
                  When you submit a website for scanning, we:
                </p>
                <ul className="list-disc list-inside mt-2 space-y-1 text-foreground/80">
                  <li>Fetch your website's public pages using standard HTTP requests</li>
                  <li>Analyze the fetched content for SEO signals, structured data, and AI crawler readiness</li>
                  <li>Store the scan results temporarily to generate your report</li>
                </ul>
              </div>
            </div>
          </section>

          {/* How We Use Your Information */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">How We Use Your Information</h2>

            <div className="space-y-3">
              <div>
                <p className="font-medium text-foreground mb-2">We use the information we collect to:</p>
                <ul className="list-disc list-inside space-y-2 text-foreground/80">
                  <li>Generate and deliver your AI visibility scan results</li>
                  <li>Communicate scan results and findings to you via email</li>
                  <li>Understand how users interact with citability.dev (via aggregated analytics)</li>
                  <li>Improve the scan accuracy, performance, and user experience</li>
                  <li>Respond to support inquiries (if you contact us)</li>
                  <li>Comply with legal obligations</li>
                  <li>Prevent fraud and abuse</li>
                </ul>
              </div>

              <div className="border border-border/50 rounded-md bg-card p-4 mt-4">
                <p className="text-xs font-medium text-teal uppercase tracking-widest mb-2">Important Note</p>
                <p className="text-sm text-foreground/80">
                  We do <strong>not</strong> use your information for marketing purposes, sell your data to third parties, or use it to train AI models. Your data is treated as confidential business information.
                </p>
              </div>
            </div>
          </section>

          {/* Third-Party Services */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Third-Party Services</h2>

            <div className="space-y-4">
              <div>
                <h3 className="font-semibold text-foreground mb-2">Vercel Analytics</h3>
                <p className="leading-relaxed text-foreground/80">
                  We use Vercel Analytics to understand how visitors use citability.dev. Vercel Analytics collects anonymized usage data such as page views, referrer sources, and aggregate traffic patterns. No personally identifiable information is shared with Vercel. For more information, see Vercel's privacy policy at <a href="https://vercel.com/legal/privacy-policy" target="_blank" rel="noopener noreferrer" className="text-teal hover:text-teal/80 transition-colors">vercel.com/legal/privacy-policy</a>.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">Email Delivery</h3>
                <p className="leading-relaxed text-foreground/80">
                  Scan results are delivered via email. Your email address may be processed by standard email infrastructure providers. We do not retain email addresses longer than necessary to deliver your scan results.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">Hosting Provider</h3>
                <p className="leading-relaxed text-foreground/80">
                  citability.dev is hosted on Vercel. Your information is stored on Vercel's servers, which are located in the United States. Vercel is a Data Processing Agreement (DPA) compliant provider.
                </p>
              </div>
            </div>
          </section>

          {/* Cookies */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Cookies</h2>
            <p className="leading-relaxed mb-3">
              We use cookies only for essential functionality and analytics:
            </p>
            <ul className="list-disc list-inside space-y-2 text-foreground/80">
              <li><strong>Essential cookies:</strong> Required for site functionality (e.g., form state)</li>
              <li><strong>Analytics cookies:</strong> Vercel Analytics uses cookies to track aggregate usage patterns. These are non-identifying and cannot be linked to you personally.</li>
            </ul>
            <p className="leading-relaxed mt-3">
              You can disable cookies in your browser settings, but this may affect the functionality of the site.
            </p>
          </section>

          {/* Data Retention */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Data Retention</h2>
            <p className="leading-relaxed">
              We retain your information only as long as necessary:
            </p>
            <ul className="list-disc list-inside mt-2 space-y-1 text-foreground/80">
              <li><strong>Scan data:</strong> Retained for 30 days to allow you to access and download your report, then deleted</li>
              <li><strong>Email addresses:</strong> Deleted after scan results are delivered, unless you opt in to future communication</li>
              <li><strong>Analytics data:</strong> Aggregated data retained per Vercel's retention policy (typically 90 days)</li>
            </ul>
          </section>

          {/* Your Rights */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Your Privacy Rights</h2>
            <p className="leading-relaxed mb-3">
              Depending on your location, you may have certain rights regarding your information:
            </p>
            <ul className="list-disc list-inside space-y-2 text-foreground/80">
              <li><strong>Access:</strong> You have the right to request a copy of your personal data</li>
              <li><strong>Correction:</strong> You have the right to request that we correct inaccurate information</li>
              <li><strong>Deletion:</strong> You have the right to request deletion of your personal data, subject to legal retention requirements</li>
              <li><strong>Opt-out:</strong> You may opt out of analytics and email communication at any time</li>
            </ul>
            <p className="leading-relaxed mt-3">
              To exercise any of these rights, please contact us at <span className="font-mono text-teal">chudi@citability.dev</span>.
            </p>
          </section>

          {/* Security */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Security</h2>
            <p className="leading-relaxed">
              We take reasonable measures to protect your information from unauthorized access, alteration, disclosure, or destruction. This includes using HTTPS encryption for all communications and secure storage practices. However, no method of transmission over the internet or electronic storage is 100% secure. We cannot guarantee absolute security, and you use citability.dev at your own risk.
            </p>
          </section>

          {/* Children's Privacy */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Children's Privacy</h2>
            <p className="leading-relaxed">
              citability.dev is not intended for use by children under 13 years of age. We do not knowingly collect personal information from children under 13. If we become aware that we have collected information from a child under 13, we will delete such information promptly.
            </p>
          </section>

          {/* Changes to This Policy */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Changes to This Privacy Policy</h2>
            <p className="leading-relaxed">
              We may update this Privacy Policy from time to time to reflect changes in our practices or legal requirements. We will notify you of material changes by posting the updated policy on this page and updating the "Last updated" date. Your continued use of citability.dev constitutes your acceptance of the updated Privacy Policy.
            </p>
          </section>

          {/* Contact */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Contact Us</h2>
            <p className="leading-relaxed mb-4">
              If you have questions about this Privacy Policy, your data, or our privacy practices, please contact us:
            </p>
            <div className="border border-border rounded-lg bg-card p-6 flex items-start gap-3">
              <Mail className="w-5 h-5 text-teal flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-mono text-sm text-teal mb-1">chudi@citability.dev</p>
                <p className="text-sm text-foreground/80">
                  Please allow 5 business days for a response.
                </p>
              </div>
            </div>
          </section>
        </div>

        {/* Footer CTA */}
        <div className="mt-16 text-center border border-border rounded-lg bg-card p-8">
          <h2 className="text-xl font-bold text-foreground mb-2">Ready to audit your AI visibility?</h2>
          <p className="text-sm text-muted-foreground mb-6">Run a free scan in 10 seconds. No account required.</p>
          <a
            href="/assess"
            className="inline-block bg-teal text-primary-foreground hover:opacity-90 font-semibold font-mono text-sm px-6 py-3 rounded-md transition-opacity"
          >
            Start Free Scan
          </a>
        </div>
      </main>
    </div>
  )
}
