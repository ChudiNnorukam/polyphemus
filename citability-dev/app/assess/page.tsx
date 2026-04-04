"use client"

import { useState, useRef } from "react"
import { ArrowRight, ArrowLeft, Check, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

type Step = "contact" | "questions" | "freetext" | "scanning"

interface ScanResult {
  url: string
  score: number
  total: number
  checks: Array<{ name: string; slug: string; pass: boolean; detail: string }>
  scannedAt: string
}

const questions = [
  {
    id: "role",
    question: "What best describes your role?",
    options: [
      { label: "Marketing Director / CMO", value: "marketing" },
      { label: "Founder / CEO", value: "founder" },
      { label: "SEO Specialist", value: "seo" },
      { label: "Developer / Technical", value: "dev" },
      { label: "Other", value: "other" },
    ],
  },
  {
    id: "frustration",
    question: "What's your biggest frustration with AI visibility right now?",
    options: [
      { label: "I don't know if AI mentions my brand at all", value: "unknown" },
      { label: "I invest in SEO but AI still ignores me", value: "seo_no_ai" },
      { label: "My competitors show up in AI but I don't", value: "competitor_gap" },
      { label: "I don't even know where to start with AI visibility", value: "no_start" },
    ],
  },
  {
    id: "tried",
    question: "What have you tried so far?",
    options: [
      { label: "Nothing yet, just learning", value: "nothing" },
      { label: "Traditional SEO (content, backlinks, technical)", value: "seo_only" },
      { label: "Added structured data or schema markup", value: "schema" },
      { label: "Tried AI-specific things (llms.txt, ai.txt)", value: "ai_specific" },
    ],
  },
  {
    id: "goal",
    question: "What would success look like in the next 90 days?",
    options: [
      { label: "Know where I stand vs competitors", value: "benchmark" },
      { label: "Get mentioned by ChatGPT or Perplexity for the first time", value: "first_mention" },
      { label: "Get actual URL citations from AI systems", value: "citations" },
      { label: "Have a complete AI visibility strategy for my team", value: "strategy" },
    ],
  },
  {
    id: "solution",
    question: "Which approach would suit you best?",
    options: [
      { label: "A free report I can act on myself", value: "diy" },
      { label: "A detailed paid report with specific recommendations", value: "report" },
      { label: "A consultation with an expert who can guide me", value: "consult" },
      { label: "Someone to handle the entire implementation", value: "done_for_you" },
    ],
  },
]

export default function AssessPage() {
  const [step, setStep] = useState<Step>("contact")
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [website, setWebsite] = useState("")
  const [phone, setPhone] = useState("")
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [freeText, setFreeText] = useState("")
  const scanRef = useRef<Promise<ScanResult | null> | null>(null)
  const [scanStatus, setScanStatus] = useState<"idle" | "scanning" | "done" | "error">("idle")

  const totalSteps = 1 + questions.length + 1 // contact + 5 questions + freetext
  const currentStep =
    step === "contact" ? 1 :
    step === "questions" ? 2 + qIndex :
    step === "freetext" ? 7 :
    7
  const progress = Math.round((currentStep / totalSteps) * 100)

  // Start scan in background when contact form is submitted
  const startBackgroundScan = (url: string) => {
    if (!url.trim()) return
    setScanStatus("scanning")
    scanRef.current = fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    })
      .then((r) => r.json() as Promise<ScanResult>)
      .then((data) => { setScanStatus("done"); return data })
      .catch(() => { setScanStatus("error"); return null })
  }

  const handleContactSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !email.trim()) return
    // Start scan immediately in background
    startBackgroundScan(website)
    setStep("questions")
  }

  const handleAnswer = (value: string) => {
    const q = questions[qIndex]
    setAnswers((prev) => ({ ...prev, [q.id]: value }))
    if (qIndex < questions.length - 1) {
      setQIndex((i) => i + 1)
    } else {
      setStep("freetext")
    }
  }

  const handleSubmit = async () => {
    setStep("scanning")

    // Wait for scan to complete if still running
    let scanData: ScanResult | null = null
    if (scanRef.current) {
      scanData = await scanRef.current
    }

    // POST submission to API
    fetch("/api/assess", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, email, website, phone,
        answers,
        score: scanData?.score ?? 0,
        freeText,
        scanResults: scanData,
      }),
    }).catch(() => {})

    // Build results URL
    const params = new URLSearchParams({
      name,
      url: website,
      goal: answers.goal || "",
      solution: answers.solution || "",
      scan: scanData ? JSON.stringify({
        score: scanData.score,
        total: scanData.total,
        checks: scanData.checks,
        url: scanData.url,
      }) : "",
    })

    window.location.href = `/results?${params.toString()}`
  }

  const handleBack = () => {
    if (step === "questions" && qIndex > 0) {
      setQIndex((i) => i - 1)
    } else if (step === "questions" && qIndex === 0) {
      setStep("contact")
    } else if (step === "freetext") {
      setQIndex(questions.length - 1)
      setStep("questions")
    }
  }

  return (
    <main className="min-h-screen bg-background text-foreground flex flex-col items-center justify-center px-4 py-16">
      {/* Home nav */}
      <a
        href="/"
        className="fixed top-5 left-6 text-sm font-mono text-muted-foreground hover:text-teal transition-colors z-50"
      >
        ← citability.dev
      </a>

      {/* Progress bar */}
      <div className="w-full max-w-xl mb-8">
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-xs text-muted-foreground">
            {step === "scanning" ? "Generating your report..." : `Step ${currentStep} of ${totalSteps}`}
          </span>
          <span className="font-mono text-xs text-teal">{progress}%</span>
        </div>
        <div className="w-full h-1.5 bg-navy-surface rounded-full overflow-hidden">
          <div
            className="h-full bg-teal rounded-full transition-all duration-300"
            style={{ width: `${step === "scanning" ? 100 : progress}%` }}
          />
        </div>
      </div>

      <div className="w-full max-w-xl">
        {/* Contact info step */}
        {step === "contact" && (
          <form onSubmit={handleContactSubmit} className="flex flex-col gap-6">
            <div>
              <h2 className="text-2xl font-semibold text-foreground mb-2">
                Let&apos;s audit your site
              </h2>
              <p className="text-sm text-muted-foreground">
                Enter your website and we&apos;ll run a real infrastructure and content-readiness scan while you answer a few questions.
              </p>
            </div>

            <div className="flex flex-col gap-4">
              <div>
                <label className="block font-mono text-xs text-muted-foreground mb-1.5">
                  Website URL <span className="text-teal">*</span>
                </label>
                <Input
                  value={website}
                  onChange={(e) => setWebsite(e.target.value)}
                  placeholder="yourdomain.com"
                  required
                  className="h-12 bg-navy-surface border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-teal font-mono text-sm"
                />
              </div>
              <div>
                <label className="block font-mono text-xs text-muted-foreground mb-1.5">
                  Name <span className="text-red-400">*</span>
                </label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                  required
                  className="h-12 bg-navy-surface border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-teal"
                />
              </div>
              <div>
                <label className="block font-mono text-xs text-muted-foreground mb-1.5">
                  Email <span className="text-red-400">*</span>
                </label>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  required
                  className="h-12 bg-navy-surface border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-teal"
                />
              </div>
              <div>
                <label className="block font-mono text-xs text-muted-foreground mb-1.5">
                  Phone <span className="text-muted-foreground/50">(optional)</span>
                </label>
                <Input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+1 (555) 000-0000"
                  className="h-12 bg-navy-surface border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-teal"
                />
              </div>
            </div>

            <Button
              type="submit"
              className="h-12 bg-teal text-primary-foreground font-semibold hover:bg-teal-dim flex items-center gap-2"
            >
              Scan My Site + Start Assessment
              <ArrowRight className="w-4 h-4" />
            </Button>

            <p className="text-center text-xs text-muted-foreground font-mono">
              We scan 10 AI visibility signals. Takes 3 minutes. Free.
            </p>
          </form>
        )}

        {/* Pain-point questions */}
        {step === "questions" && (
          <div className="flex flex-col gap-6">
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="font-mono text-xs text-teal">
                  Question {qIndex + 1} of {questions.length}
                </span>
                {scanStatus === "scanning" && (
                  <span className="inline-flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Scanning your site...
                  </span>
                )}
                {scanStatus === "done" && (
                  <span className="inline-flex items-center gap-1.5 font-mono text-xs text-teal">
                    <Check className="w-3 h-3" />
                    Scan complete
                  </span>
                )}
              </div>
              <h2 className="text-xl font-semibold text-foreground leading-relaxed">
                {questions[qIndex].question}
              </h2>
            </div>

            <div className="flex flex-col gap-3">
              {questions[qIndex].options.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handleAnswer(opt.value)}
                  className={cn(
                    "text-left px-5 py-4 rounded-lg border transition-all duration-200",
                    "border-border bg-navy-surface hover:border-teal/50 hover:bg-teal/5",
                    "text-foreground text-sm"
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            <button
              onClick={handleBack}
              className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors self-start"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
          </div>
        )}

        {/* Free text + submit */}
        {step === "freetext" && (
          <div className="flex flex-col gap-6">
            <div>
              <span className="font-mono text-xs text-teal">Final Question</span>
              <h2 className="mt-2 text-xl font-semibold text-foreground leading-relaxed">
                Is there anything else we should know?
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Optional. People often share the most useful context here.
              </p>
            </div>

            <textarea
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              placeholder="E.g., 'My competitor just got cited by ChatGPT' or 'I have budget this quarter'"
              rows={4}
              className="w-full px-4 py-3 rounded-lg border border-border bg-navy-surface text-foreground text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-teal resize-none"
            />

            <Button
              onClick={handleSubmit}
              className="h-12 bg-teal text-primary-foreground font-semibold hover:bg-teal-dim flex items-center gap-2"
            >
              See My Report
              <Check className="w-4 h-4" />
            </Button>

            <button
              onClick={handleBack}
              className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors self-start"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
          </div>
        )}

        {/* Scanning / loading state */}
        {step === "scanning" && (
          <div className="flex flex-col items-center gap-6 py-12">
            <Loader2 className="w-10 h-10 text-teal animate-spin" />
            <div className="text-center">
              <h2 className="text-xl font-semibold text-foreground mb-2">
                Generating your report...
              </h2>
              <p className="text-sm text-muted-foreground">
                Analyzing {website || "your site"} across 10 AI visibility signals.
              </p>
            </div>
          </div>
        )}
      </div>

      <p className="mt-12 font-mono text-xs text-muted-foreground/50">
        citability.dev — AI Visibility Assessment
      </p>
    </main>
  )
}
