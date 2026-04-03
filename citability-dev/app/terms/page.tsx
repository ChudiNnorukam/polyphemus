import type { Metadata } from "next"
import { Mail } from "lucide-react"

export const metadata: Metadata = {
  title: "Terms of Service - citability.dev",
  description:
    "Terms of Service for citability.dev. Our service agreement, acceptable use policy, and limitations of liability.",
}

export default function TermsPage() {
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
          <span className="font-mono text-xs text-teal tracking-widest uppercase">Terms of Service</span>
          <h1 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground">
            Terms of Service
          </h1>
          <p className="mt-4 text-sm text-muted-foreground">
            Last updated: April 3, 2026
          </p>
        </div>

        {/* Content */}
        <div className="prose prose-invert max-w-none text-sm text-foreground/90 space-y-8">
          {/* Agreement to Terms */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Agreement to Terms</h2>
            <p className="leading-relaxed">
              These Terms of Service ("Terms") constitute a legally binding agreement between you (the "User," "you," or "your") and citability.dev (the "Service," "we," "our," or "us"), operated by Chudi Nnorukam.
            </p>
            <p className="leading-relaxed mt-3">
              By accessing or using citability.dev, you agree to be bound by all the terms and conditions of this agreement. If you do not agree to these Terms, you may not use the Service.
            </p>
          </section>

          {/* Service Description */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Service Description</h2>
            <p className="leading-relaxed mb-3">
              citability.dev provides an automated AI visibility readiness audit tool (the "Service"). The Service:
            </p>
            <ul className="list-disc list-inside space-y-2 text-foreground/80">
              <li>Scans websites for infrastructure signals that affect AI crawler discoverability and crawlability</li>
              <li>Analyzes technical readiness across 10 automated checks (robots.txt, sitemap, structured data, HTTPS, etc.)</li>
              <li>Delivers results via email within 10 seconds of submission</li>
              <li>Is provided on a free, no-account-required basis</li>
            </ul>
            <p className="leading-relaxed mt-3">
              The Service is provided on an "as-is" basis. We make no warranty regarding the accuracy, completeness, or utility of the scan results for any particular use case.
            </p>
          </section>

          {/* Eligibility */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Eligibility</h2>
            <p className="leading-relaxed mb-3">
              To use citability.dev, you must:
            </p>
            <ul className="list-disc list-inside space-y-2 text-foreground/80">
              <li>Be at least 13 years of age</li>
              <li>Have the right to scan the website you submit (i.e., you own it or have authorization)</li>
              <li>Comply with all applicable laws and regulations</li>
              <li>Not be located in a jurisdiction where the Service is prohibited</li>
            </ul>
          </section>

          {/* Acceptable Use */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Acceptable Use Policy</h2>
            <p className="leading-relaxed mb-3">
              You agree not to use citability.dev for any of the following purposes:
            </p>
            <ul className="list-disc list-inside space-y-2 text-foreground/80">
              <li>Scanning websites you do not own or have authorization to scan (excluding public websites for research)</li>
              <li>Attempting to overload, crash, or disrupt the Service through automated requests or denial-of-service attacks</li>
              <li>Attempting to reverse-engineer, decompile, or access the underlying technology or algorithms</li>
              <li>Scraping, caching, or storing scan results beyond personal use</li>
              <li>Using the Service to develop competing products or services</li>
              <li>Violating any applicable laws, regulations, or third-party intellectual property rights</li>
              <li>Engaging in harassment, abuse, or threatening behavior toward citability.dev or its operators</li>
            </ul>
            <p className="leading-relaxed mt-3">
              Violating this Acceptable Use Policy may result in immediate suspension or termination of your access to the Service.
            </p>
          </section>

          {/* Intellectual Property */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Intellectual Property Rights</h2>

            <div className="space-y-4">
              <div>
                <h3 className="font-semibold text-foreground mb-2">Our Intellectual Property</h3>
                <p className="leading-relaxed text-foreground/80">
                  citability.dev, including its design, functionality, algorithms, and content (excluding user-provided data), is owned by Chudi Nnorukam and protected by copyright and other intellectual property laws. You are granted a non-exclusive, non-transferable license to use the Service solely for your personal, non-commercial purposes.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">Your Website Data</h3>
                <p className="leading-relaxed text-foreground/80">
                  You retain all rights to your website and any data you submit for scanning. By submitting a website URL, you grant us the right to fetch and analyze its publicly accessible content for the purpose of generating your scan report.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">Scan Results</h3>
                <p className="leading-relaxed text-foreground/80">
                  Scan results are provided for your personal use. You may not republish, redistribute, or commercialize the scan results without explicit written consent from citability.dev.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">Open Source Framework</h3>
                <p className="leading-relaxed text-foreground/80">
                  The underlying AI readiness scanning framework is available open-source under the Apache 2.0 license at <a href="https://github.com/ChudiNnorukam/ai-visibility-readiness" target="_blank" rel="noopener noreferrer" className="text-teal hover:text-teal/80 transition-colors">github.com/ChudiNnorukam/ai-visibility-readiness</a>. Use of the open-source framework is governed by the Apache 2.0 license, not these Terms.
                </p>
              </div>
            </div>
          </section>

          {/* Limitation of Liability */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Limitation of Liability</h2>

            <div className="space-y-4">
              <div>
                <h3 className="font-semibold text-foreground mb-2">Disclaimer of Warranties</h3>
                <p className="leading-relaxed text-foreground/80">
                  THE SERVICE IS PROVIDED ON AN "AS-IS" BASIS WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED. WE DISCLAIM ALL WARRANTIES, INCLUDING BUT NOT LIMITED TO:
                </p>
                <ul className="list-disc list-inside mt-2 space-y-1 text-foreground/80">
                  <li>Accuracy, completeness, or correctness of scan results</li>
                  <li>Fitness for a particular purpose</li>
                  <li>Non-infringement of third-party rights</li>
                  <li>Uninterrupted or error-free operation</li>
                </ul>
              </div>

              <div className="border border-border/50 rounded-md bg-card p-4">
                <p className="font-mono text-xs text-teal tracking-widest uppercase mb-2">Important Note</p>
                <p className="text-sm text-foreground/80">
                  Scan results are based on publicly available website infrastructure and should not be treated as legal advice or a guarantee of AI discoverability. AI crawler behavior, indexing policies, and training practices are subject to change. Always verify results with official documentation from Google, OpenAI, Anthropic, or other AI system operators.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">Limitation of Damages</h3>
                <p className="leading-relaxed text-foreground/80">
                  TO THE MAXIMUM EXTENT PERMITTED BY LAW, IN NO EVENT SHALL CITABILITY.DEV OR ITS OPERATORS BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, INCLUDING BUT NOT LIMITED TO LOST PROFITS, LOST REVENUE, LOST DATA, OR BUSINESS INTERRUPTION, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">Total Liability Cap</h3>
                <p className="leading-relaxed text-foreground/80">
                  OUR TOTAL LIABILITY TO YOU FOR ANY CLAIM ARISING OUT OF OR RELATING TO THIS SERVICE SHALL NOT EXCEED $100 USD.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-foreground mb-2">No Liability for Third-Party Content</h3>
                <p className="leading-relaxed text-foreground/80">
                  We are not responsible for the accuracy, legality, or copyright compliance of any content on websites you submit for scanning. If you believe a website violates your intellectual property rights, contact the website owner directly or submit a complaint to the appropriate authority.
                </p>
              </div>
            </div>
          </section>

          {/* Indemnification */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Indemnification</h2>
            <p className="leading-relaxed">
              You agree to indemnify, defend, and hold harmless citability.dev and its operators from any and all claims, damages, losses, or expenses (including attorneys' fees) arising from or relating to: (1) your violation of these Terms, (2) your use of the Service, (3) your submission of content for scanning, or (4) your infringement of any third-party rights.
            </p>
          </section>

          {/* Service Availability */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Service Availability and Maintenance</h2>
            <p className="leading-relaxed mb-3">
              We strive to maintain citability.dev with high availability, but we make no guarantee of uninterrupted service. The Service may be temporarily unavailable for:
            </p>
            <ul className="list-disc list-inside space-y-1 text-foreground/80">
              <li>Scheduled maintenance and updates</li>
              <li>Emergency repairs or security patches</li>
              <li>Infrastructure issues beyond our control</li>
            </ul>
            <p className="leading-relaxed mt-3">
              We will attempt to provide notice of planned maintenance when possible, but we are not liable for downtime or service interruptions.
            </p>
          </section>

          {/* Termination */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Termination of Service</h2>
            <p className="leading-relaxed mb-3">
              We may terminate or suspend your access to citability.dev immediately and without notice if:
            </p>
            <ul className="list-disc list-inside space-y-1 text-foreground/80">
              <li>You violate these Terms or any applicable law</li>
              <li>You engage in abuse, harassment, or fraudulent activity</li>
              <li>Your use negatively impacts the Service or other users</li>
            </ul>
            <p className="leading-relaxed mt-3">
              Termination will result in the deletion of your scan data and removal of your access. Termination does not affect your indemnification obligations or our liability limitations.
            </p>
          </section>

          {/* Changes to Terms */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Changes to These Terms</h2>
            <p className="leading-relaxed">
              We may update these Terms from time to time to reflect changes in our practices, technology, or legal requirements. We will post the updated Terms on this page and update the "Last updated" date. Material changes will be communicated via email or prominent notice on the Service. Your continued use of citability.dev following the posting of updated Terms constitutes your acceptance of the changes. If you do not agree to any changes, you must discontinue use of the Service.
            </p>
          </section>

          {/* Governing Law */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Governing Law and Jurisdiction</h2>
            <p className="leading-relaxed">
              These Terms are governed by the laws of the State of California, without regard to its conflict-of-law principles. Any legal action or proceeding arising under or relating to these Terms shall be subject to the exclusive jurisdiction of the state and federal courts located in California, and you irrevocably consent to the jurisdiction and venue of such courts.
            </p>
          </section>

          {/* Severability */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Severability</h2>
            <p className="leading-relaxed">
              If any provision of these Terms is found to be invalid or unenforceable by a court of competent jurisdiction, the remaining provisions shall remain in effect, and the invalid provision shall be modified to the minimum extent necessary to make it valid and enforceable.
            </p>
          </section>

          {/* Entire Agreement */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Entire Agreement</h2>
            <p className="leading-relaxed">
              These Terms, together with our Privacy Policy, constitute the entire agreement between you and citability.dev regarding the Service. They supersede any prior or contemporaneous agreements, understandings, or negotiations, whether oral or written. In the event of a conflict between these Terms and the Privacy Policy, these Terms shall govern.
            </p>
          </section>

          {/* Contact */}
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-4">Contact for Questions</h2>
            <p className="leading-relaxed mb-4">
              If you have questions about these Terms of Service, please contact us:
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
          <h2 className="text-xl font-bold text-foreground mb-2">Run your free AI visibility audit</h2>
          <p className="text-sm text-muted-foreground mb-6">Scan any website in 10 seconds. No account, no strings.</p>
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
