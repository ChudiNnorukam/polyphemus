"use client"

import { useState } from "react"
import { ArrowRight, ArrowLeft, Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

type Step = "contact" | "practices" | "qualifying" | "submitting"

const bestPractices = [
  {
    id: "q1",
    question: "Does your website have a robots.txt file that allows search engine and AI crawlers?",
    options: [
      { label: "Yes", value: 1 },
      { label: "No", value: 0 },
      { label: "I don't know", value: 0 },
    ],
  },
  {
    id: "q2",
    question: "Does your website have a sitemap.xml with up-to-date lastmod dates?",
    options: [
      { label: "Yes", value: 1 },
      { label: "No", value: 0 },
      { label: "I don't know", value: 0 },
    ],
  },
  {
    id: "q3",
    question: "Does your website use structured data (JSON-LD schema) on key pages?",
    options: [
      { label: "Yes, on most pages", value: 1 },
      { label: "On some pages", value: 0.5 },
      { label: "No", value: 0 },
      { label: "I don't know", value: 0 },
    ],
  },
  {
    id: "q4",
    question: "Does your website have an /llms.txt file for AI discovery?",
    options: [
      { label: "Yes", value: 1 },
      { label: "No", value: 0 },
      { label: "What is /llms.txt?", value: 0 },
    ],
  },
  {
    id: "q5",
    question: "Do your blog posts use question-format headings (e.g., 'How does X work?')?",
    options: [
      { label: "Yes, consistently", value: 1 },
      { label: "Sometimes", value: 0.5 },
      { label: "Rarely or never", value: 0 },
    ],
  },
  {
    id: "q6",
    question: "Does your content include original data, case studies, or measured results?",
    options: [
      { label: "Yes, most content has original insights", value: 1 },
      { label: "Some does", value: 0.5 },
      { label: "Mostly definitional content", value: 0 },
    ],
  },
  {
    id: "q7",
    question: "Do you monitor whether AI systems mention your brand?",
    options: [
      { label: "Yes, I check regularly", value: 1 },
      { label: "I've checked once or twice", value: 0.5 },
      { label: "Never", value: 0 },
    ],
  },
  {
    id: "q8",
    question: "Does your content have clear 'answer-first' introductions that directly answer the page's core question?",
    options: [
      { label: "Yes", value: 1 },
      { label: "Sometimes", value: 0.5 },
      { label: "No, we start with background/context", value: 0 },
    ],
  },
  {
    id: "q9",
    question: "Do your pages include specific numbers, methodologies, or named frameworks?",
    options: [
      { label: "Yes, consistently", value: 1 },
      { label: "Occasionally", value: 0.5 },
      { label: "Rarely", value: 0 },
    ],
  },
  {
    id: "q10",
    question: "Does your website have an /ai.txt file specifying AI crawler policies?",
    options: [
      { label: "Yes", value: 1 },
      { label: "No", value: 0 },
      { label: "What is /ai.txt?", value: 0 },
    ],
  },
]

const qualifyingQuestions = [
  {
    id: "q11",
    question: "Which best describes your current SEO situation?",
    options: [
      { label: "Just getting started with SEO", value: "beginner" },
      { label: "Some organic traffic but want more", value: "growing" },
      { label: "Strong SEO, want to add AI visibility", value: "advanced" },
      { label: "Established brand maintaining position", value: "established" },
    ],
  },
  {
    id: "q12",
    question: "What's the most important outcome you want in the next 90 days?",
    options: [
      { label: "Get mentioned by AI for the first time", value: "first_mention" },
      { label: "Understand where I stand vs competitors", value: "benchmark" },
      { label: "Get actual URL citations from AI systems", value: "citations" },
      { label: "Build a complete AI visibility strategy", value: "strategy" },
    ],
  },
  {
    id: "q13",
    question: "What's the biggest obstacle you've faced?",
    options: [
      { label: "I don't know where to start", value: "awareness" },
      { label: "I've done SEO but AI still doesn't mention us", value: "gap" },
      { label: "I don't have the time or resources", value: "resources" },
      { label: "I'm not sure AI visibility matters yet", value: "skeptic" },
    ],
  },
  {
    id: "q14",
    question: "Which approach would suit you best?",
    options: [
      { label: "A free guide or checklist I can follow", value: "diy" },
      { label: "A detailed report with specific recommendations", value: "report" },
      { label: "A consultation with an expert", value: "consult" },
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
  const [practiceIndex, setPracticeIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, number | string>>({})
  const [qualIndex, setQualIndex] = useState(0)
  const [freeText, setFreeText] = useState("")

  const totalSteps = 1 + bestPractices.length + qualifyingQuestions.length + 1 // contact + 10 + 4 + freetext
  const currentStep =
    step === "contact"
      ? 1
      : step === "practices"
        ? 2 + practiceIndex
        : step === "qualifying"
          ? 12 + qualIndex
          : 16

  const progress = Math.round((currentStep / totalSteps) * 100)

  const handleContactSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !email.trim()) return
    setStep("practices")
  }

  const handlePracticeAnswer = (value: number) => {
    const q = bestPractices[practiceIndex]
    setAnswers((prev) => ({ ...prev, [q.id]: value }))
    if (practiceIndex < bestPractices.length - 1) {
      setPracticeIndex((i) => i + 1)
    } else {
      setStep("qualifying")
    }
  }

  const handleQualAnswer = (value: string) => {
    const q = qualifyingQuestions[qualIndex]
    setAnswers((prev) => ({ ...prev, [q.id]: value }))
    if (qualIndex < qualifyingQuestions.length - 1) {
      setQualIndex((i) => i + 1)
    } else {
      setStep("submitting")
    }
  }

  const handleSubmit = () => {
    // Calculate score from best practices (Q1-Q10)
    let score = 0
    bestPractices.forEach((q) => {
      const val = answers[q.id]
      if (typeof val === "number") score += val
    })

    // Build results URL with params
    const params = new URLSearchParams({
      score: score.toFixed(1),
      name: name,
      q12: (answers.q12 as string) || "",
      q14: (answers.q14 as string) || "",
      // Pass individual answers for insights
      ...Object.fromEntries(
        bestPractices.map((q) => [q.id, String(answers[q.id] ?? 0)])
      ),
    })

    window.location.href = `/results?${params.toString()}`
  }

  const handleBack = () => {
    if (step === "practices" && practiceIndex > 0) {
      setPracticeIndex((i) => i - 1)
    } else if (step === "practices" && practiceIndex === 0) {
      setStep("contact")
    } else if (step === "qualifying" && qualIndex > 0) {
      setQualIndex((i) => i - 1)
    } else if (step === "qualifying" && qualIndex === 0) {
      setPracticeIndex(bestPractices.length - 1)
      setStep("practices")
    } else if (step === "submitting") {
      setQualIndex(qualifyingQuestions.length - 1)
      setStep("qualifying")
    }
  }

  return (
    <main className="min-h-screen bg-background text-foreground flex flex-col items-center justify-center px-4 py-16">
      {/* Progress bar */}
      <div className="w-full max-w-xl mb-8">
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-xs text-muted-foreground">
            Question {currentStep} of {totalSteps}
          </span>
          <span className="font-mono text-xs text-teal">{progress}%</span>
        </div>
        <div className="w-full h-1.5 bg-navy-surface rounded-full overflow-hidden">
          <div
            className="h-full bg-teal rounded-full transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="w-full max-w-xl">
        {/* Contact info step */}
        {step === "contact" && (
          <form onSubmit={handleContactSubmit} className="flex flex-col gap-6">
            <div>
              <h2 className="text-2xl font-semibold text-foreground mb-2">
                Let&apos;s get started
              </h2>
              <p className="text-sm text-muted-foreground">
                Enter your details so we can personalize your results.
              </p>
            </div>

            <div className="flex flex-col gap-4">
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
                  Website URL
                </label>
                <Input
                  value={website}
                  onChange={(e) => setWebsite(e.target.value)}
                  placeholder="https://yourdomain.com"
                  className="h-12 bg-navy-surface border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-teal font-mono text-sm"
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
              Start Assessment
              <ArrowRight className="w-4 h-4" />
            </Button>
          </form>
        )}

        {/* Best practice questions */}
        {step === "practices" && (
          <div className="flex flex-col gap-6">
            <div>
              <span className="font-mono text-xs text-teal">
                Best Practices ({practiceIndex + 1}/10)
              </span>
              <h2 className="mt-2 text-xl font-semibold text-foreground leading-relaxed">
                {bestPractices[practiceIndex].question}
              </h2>
            </div>

            <div className="flex flex-col gap-3">
              {bestPractices[practiceIndex].options.map((opt) => (
                <button
                  key={opt.label}
                  onClick={() => handlePracticeAnswer(opt.value)}
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

        {/* Qualifying questions */}
        {step === "qualifying" && (
          <div className="flex flex-col gap-6">
            <div>
              <span className="font-mono text-xs text-teal">
                About You ({qualIndex + 1}/4)
              </span>
              <h2 className="mt-2 text-xl font-semibold text-foreground leading-relaxed">
                {qualifyingQuestions[qualIndex].question}
              </h2>
            </div>

            <div className="flex flex-col gap-3">
              {qualifyingQuestions[qualIndex].options.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handleQualAnswer(opt.value)}
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
        {step === "submitting" && (
          <div className="flex flex-col gap-6">
            <div>
              <span className="font-mono text-xs text-teal">Final Question</span>
              <h2 className="mt-2 text-xl font-semibold text-foreground leading-relaxed">
                Is there anything else we should know about your situation?
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Optional, but people often share the most useful context here.
              </p>
            </div>

            <textarea
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              placeholder="E.g., 'My competitor just got cited by ChatGPT' or 'I have budget to spend this quarter'"
              rows={4}
              className="w-full px-4 py-3 rounded-lg border border-border bg-navy-surface text-foreground text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-teal resize-none"
            />

            <Button
              onClick={handleSubmit}
              className="h-12 bg-teal text-primary-foreground font-semibold hover:bg-teal-dim flex items-center gap-2"
            >
              See My Results
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
      </div>

      {/* Footer */}
      <p className="mt-12 font-mono text-xs text-muted-foreground/50">
        citability.dev — AI Visibility Assessment
      </p>
    </main>
  )
}
