#!/usr/bin/env python3
"""Generate a LinkedIn carousel PDF from blog content.

Creates a 1080x1080px (LinkedIn optimal) multi-slide PDF carousel.
Uses reportlab for PDF generation. Professional design matching
top AI/tech LinkedIn creators.
"""

from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from pathlib import Path
import textwrap

# LinkedIn carousel = square slides, 1080x1080px = 7.5x7.5 inches at 144dpi
W = 7.5 * inch
H = 7.5 * inch

# Brand colors (chudi.dev Stitch design system)
PRIMARY = HexColor("#1162d4")
DARK_BG = HexColor("#0f1923")
SURFACE = HexColor("#172231")
SURFACE_LIGHT = HexColor("#1e2d40")
WHITE = HexColor("#FFFFFF")
LIGHT_TEXT = HexColor("#8fa8c8")
CYAN = HexColor("#22d3ee")
GREEN = HexColor("#34d399")
AMBER = HexColor("#fbbf24")

# Layout constants
MARGIN = 60
CONTENT_W = W - (MARGIN * 2)


def bg(c, color=DARK_BG):
    """Fill background."""
    c.setFillColor(color)
    c.rect(0, 0, W, H, fill=1, stroke=0)


def accent_dots(c):
    """Subtle decorative dot grid in corner (inspired by pro tech carousels)."""
    c.setFillColor(HexColor("#1a2a3d"))
    for i in range(5):
        for j in range(5):
            c.circle(W - 36 - i * 14, H - 36 - j * 14, 2.5, fill=1, stroke=0)


def branding(c, light=False):
    """Bottom branding strip."""
    text_color = LIGHT_TEXT if not light else HexColor("#b8d4f0")
    line_color = PRIMARY if not light else WHITE

    c.setStrokeColor(line_color)
    c.setLineWidth(1.5)
    c.line(MARGIN, 52, W - MARGIN, 52)

    c.setFillColor(text_color)
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN, 30, "chudi.dev")
    c.drawRightString(W - MARGIN, 30, "@chudi_nnorukam")


def slide_num(c, n, total, light=False):
    """Slide number badge."""
    color = LIGHT_TEXT if not light else HexColor("#b8d4f0")
    c.setFillColor(color)
    c.setFont("Helvetica", 10)
    c.drawRightString(W - MARGIN, H - 35, f"{n}/{total}")


def label(c, text, y, color=CYAN):
    """Small uppercase label."""
    c.setFillColor(color)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(MARGIN, y, text.upper())


def heading(c, lines, y, size=32):
    """Large heading, returns y after last line."""
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", size)
    for line in lines:
        c.drawString(MARGIN, y, line)
        y -= size + 8
    return y


def body_text(c, text, y, color=LIGHT_TEXT, size=14, bold=False):
    """Body text with wrapping. Returns y after text."""
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFillColor(color)
    c.setFont(font, size)
    wrapped = textwrap.wrap(text, width=48 if size >= 14 else 55)
    for line in wrapped:
        c.drawString(MARGIN, y, line)
        y -= size + 6
    return y


def card(c, x, y, w, h, color=SURFACE):
    """Rounded card background."""
    c.setFillColor(color)
    c.roundRect(x, y, w, h, 12, fill=1, stroke=0)


def pill(c, text, x, y, bg_color=PRIMARY, text_color=WHITE):
    """Small pill/badge."""
    c.setFillColor(bg_color)
    tw = len(text) * 7 + 16
    c.roundRect(x, y - 6, tw, 22, 11, fill=1, stroke=0)
    c.setFillColor(text_color)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + 8, y, text)


def number_circle(c, num, x, y, size=16, bg_color=PRIMARY):
    """Numbered circle."""
    c.setFillColor(bg_color)
    c.circle(x, y, size, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(x, y - 5, str(num))


def code_line(c, text, y):
    """Code-style highlighted line."""
    card(c, MARGIN, y - 6, CONTENT_W, 28, SURFACE_LIGHT)
    c.setFillColor(CYAN)
    c.setFont("Courier", 12)
    c.drawString(MARGIN + 14, y, text)
    return y - 38


# ===== SLIDES =====

TOTAL = 10


def slide_1_cover(c):
    bg(c)
    accent_dots(c)

    # Large watermark number (inspired by Ajay Singh's carousel)
    c.setFillColor(HexColor("#111d2a"))
    c.setFont("Helvetica-Bold", 180)
    c.drawRightString(W - 30, H - 280, "7")

    # Top label pills (pro pattern: category tags)
    y = H - 80
    pill(c, "AI CODING", MARGIN, y, SURFACE_LIGHT, CYAN)
    pill(c, "ADHD", MARGIN + 100, y, SURFACE_LIGHT, GREEN)
    pill(c, "PRODUCTIVITY", MARGIN + 170, y, SURFACE_LIGHT, LIGHT_TEXT)

    # Main title with accent-colored keyword
    y -= 55
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 38)
    c.drawString(MARGIN, y, "7 Claude Code")
    y -= 50
    c.drawString(MARGIN, y, "Workflows for")
    y -= 50
    c.setFillColor(CYAN)
    c.drawString(MARGIN, y, "ADHD Developers")

    # Subtitle
    y -= 40
    c.setFillColor(LIGHT_TEXT)
    c.setFont("Helvetica", 15)
    c.drawString(MARGIN, y, "Replace chaos with systems that")
    y -= 22
    c.drawString(MARGIN, y, "work WITH your brain, not against it.")

    # Author block (tighter to subtitle)
    y -= 50
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN, y, "Chudi Nnorukam")
    c.setFillColor(LIGHT_TEXT)
    c.setFont("Helvetica", 12)
    c.drawString(MARGIN, y - 21, "AI Systems Engineer  |  chudi.dev")

    branding(c)


def slide_2_problem(c):
    bg(c)
    slide_num(c, 2, TOTAL)
    accent_dots(c)

    label(c, "THE PROBLEM", H - 80)
    heading(c, ["Your ADHD Brain", "vs. Your IDE"], H - 120, size=30)

    items = [
        ("Task Initiation Paralysis",
         "Blank editor = frozen. You KNOW what to do but cannot START."),
        ("Context Switching Cost",
         "Every interruption = 23 min to recover. ADHD makes switches involuntary."),
        ("Time Perception Distortion",
         '"Fix one thing" becomes 4 hours. Time blindness is real.'),
        ("Documentation Fatigue",
         "Low-novelty repetitive writing drains your executive function."),
    ]

    y = H - 225
    for i, (title, desc) in enumerate(items):
        card(c, MARGIN, y - 10, CONTENT_W, 65, SURFACE)

        # Left accent bar
        c.setFillColor(PRIMARY)
        c.rect(MARGIN, y - 10, 4, 65, fill=1, stroke=0)

        number_circle(c, i + 1, MARGIN + 30, y + 30, size=14, bg_color=PRIMARY)

        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(MARGIN + 52, y + 33, title)

        c.setFillColor(LIGHT_TEXT)
        c.setFont("Helvetica", 11)
        wrapped = textwrap.wrap(desc, width=52)
        ty = y + 12
        for line in wrapped:
            c.drawString(MARGIN + 52, ty, line)
            ty -= 15

        y -= 85

    branding(c)


def slide_3_solution(c):
    bg(c)
    slide_num(c, 3, TOTAL)
    accent_dots(c)

    label(c, "THE SOLUTION", H - 80)
    heading(c, ["Claude Code Offloads", "Executive Function"], H - 120, size=28)

    items = [
        ("Context Caching",
         "Claude remembers your project across sessions. No re-explaining ever."),
        ("CLAUDE.md Templates",
         "One file = your external brain. Claude reads it automatically."),
        ("Multi-Agent Orchestration",
         "Goals decomposed into atomic tasks. Pick one and start."),
    ]

    y = H - 250
    for i, (title, desc) in enumerate(items):
        card(c, MARGIN, y - 8, CONTENT_W, 60, SURFACE)
        number_circle(c, i + 1, MARGIN + 28, y + 25, size=14)

        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(MARGIN + 52, y + 30, title)

        c.setFillColor(LIGHT_TEXT)
        c.setFont("Helvetica", 12)
        c.drawString(MARGIN + 52, y + 8, desc)

        y -= 80

    # Key insight
    card(c, MARGIN, 70, CONTENT_W, 60, SURFACE_LIGHT)
    c.setFillColor(CYAN)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN + 16, 108, "KEY INSIGHT")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 13)
    c.drawString(MARGIN + 16, 86, "You keep the engineering. Claude handles the executive function.")

    branding(c)


def slide_step(c, step_num, slide_num_val, title, bullets, tip=None):
    """Reusable step slide. bullets = list of (text, style) tuples.
    style: 'body', 'bold', 'code', 'gap'
    """
    bg(c)
    slide_num(c, slide_num_val, TOTAL)
    accent_dots(c)

    label(c, f"STEP {step_num} OF 5", H - 80)
    y = heading(c, [title] if len(title) <= 28 else title.split(" | "), H - 120, size=28)

    y -= 20

    for text, style in bullets:
        if style == "gap":
            y -= 10
        elif style == "code":
            y = code_line(c, text, y)
        elif style == "bold":
            c.setFillColor(WHITE)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(MARGIN, y, text)
            y -= 22
        elif style == "bold_sub":
            c.setFillColor(WHITE)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(MARGIN + 10, y, text)
            y -= 20
        else:  # body
            c.setFillColor(LIGHT_TEXT)
            c.setFont("Helvetica", 13)
            wrapped = textwrap.wrap(text, width=52)
            for line in wrapped:
                c.drawString(MARGIN, y, line)
                y -= 18

    if tip:
        tip_y = 75
        card(c, MARGIN, tip_y - 8, CONTENT_W, 50, SURFACE_LIGHT)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(MARGIN + 14, tip_y + 22, "PRO TIP")
        c.setFillColor(WHITE)
        c.setFont("Helvetica", 11)
        wrapped = textwrap.wrap(tip, width=58)
        ty = tip_y + 5
        for line in wrapped:
            c.drawString(MARGIN + 14, ty, line)
            ty -= 14

    branding(c)


def slide_5_rules(c):
    """Step 2: ADHD-Friendly Rules - card-based layout."""
    bg(c)
    slide_num(c, 5, TOTAL)
    accent_dots(c)

    label(c, "STEP 2 OF 5", H - 80)
    heading(c, ["Define ADHD-Friendly Rules"], H - 120, size=28)

    rules = [
        ("One Question Rule", "Never ask multiple questions in one message.", CYAN),
        ("Evidence-First Completion", "Require build output, not 'should work' claims.", GREEN),
        ("45-Minute Checkpoints", "Pause and save state. Beats time blindness.", AMBER),
        ("Task Atomicity", "Break work into 45-min chunks. Ship one at a time.", PRIMARY),
    ]

    y = H - 200
    for title, desc, accent in rules:
        card(c, MARGIN, y - 6, CONTENT_W, 50, SURFACE)
        c.setFillColor(accent)
        c.rect(MARGIN, y - 6, 4, 50, fill=1, stroke=0)

        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(MARGIN + 18, y + 26, title)

        c.setFillColor(LIGHT_TEXT)
        c.setFont("Helvetica", 11)
        c.drawString(MARGIN + 18, y + 8, desc)

        y -= 62

    # Bottom insight
    card(c, MARGIN, 68, CONTENT_W, 48, SURFACE_LIGHT)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN + 14, 96, "KEY")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 12)
    c.drawString(MARGIN + 14, 78, "These are YOUR rules. Customize them for how YOUR brain works.")

    branding(c)


def slide_6_decompose(c):
    """Step 3: Let Claude Decompose Work - before/after with code cards."""
    bg(c)
    slide_num(c, 6, TOTAL)
    accent_dots(c)

    label(c, "STEP 3 OF 5", H - 80)
    heading(c, ["Let Claude Decompose Work"], H - 120, size=28)

    # Before card (red-tinted)
    y = H - 195
    card(c, MARGIN, y - 6, CONTENT_W, 48, SURFACE)
    c.setFillColor(HexColor("#e05252"))
    c.rect(MARGIN, y - 6, 4, 48, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(MARGIN + 18, y + 24, "BEFORE")
    c.setFillColor(LIGHT_TEXT)
    c.setFont("Helvetica", 11)
    c.drawString(MARGIN + 18, y + 6, "30 min breaking tasks down. Overwhelmed. Give up.")

    # After card (green-tinted)
    y -= 60
    card(c, MARGIN, y - 6, CONTENT_W, 48, SURFACE)
    c.setFillColor(GREEN)
    c.rect(MARGIN, y - 6, 4, 48, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(MARGIN + 18, y + 24, "AFTER")
    c.setFillColor(LIGHT_TEXT)
    c.setFont("Helvetica", 11)
    c.drawString(MARGIN + 18, y + 6, "Tell Claude the goal. It returns a numbered list:")

    # Code task list
    y -= 60
    tasks = ["1. Define auth schema", "2. Create login endpoint",
             "3. Add JWT middleware", "4. Write tests"]
    for task in tasks:
        card(c, MARGIN, y - 3, CONTENT_W, 24, SURFACE_LIGHT)
        c.setFillColor(CYAN)
        c.setFont("Courier", 11)
        c.drawString(MARGIN + 14, y + 2, task)
        y -= 30

    # Bold takeaway
    y -= 4
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN, y, "Pick task #1 and start. That is it.")

    # Pro tip
    card(c, MARGIN, 68, CONTENT_W, 48, SURFACE_LIGHT)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN + 14, 96, "PRO TIP")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 11)
    c.drawString(MARGIN + 14, 78, "Choosing from a list is 100x easier than a blank slate.")

    branding(c)


def slide_7_context(c):
    """Step 4: Beat Context Switching - session cards."""
    bg(c)
    slide_num(c, 7, TOTAL)
    accent_dots(c)

    label(c, "STEP 4 OF 5", H - 80)
    heading(c, ["Beat Context", "Switching"], H - 120, size=28)

    # Session 1 card
    y = H - 235
    card(c, MARGIN, y - 8, CONTENT_W, 70, SURFACE)
    c.setFillColor(GREEN)
    c.rect(MARGIN, y - 8, 4, 70, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN + 18, y + 40, "Session 1 (45 min)")
    c.setFillColor(LIGHT_TEXT)
    c.setFont("Helvetica", 12)
    c.drawString(MARGIN + 18, y + 18, "Implement auth schema. Update checkpoint.")
    c.setFillColor(GREEN)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN + 18, y - 1, "State saved to CLAUDE.md")

    # Interruption divider
    y -= 90
    c.setFillColor(HexColor("#e05252"))
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W / 2, y + 10, "--- Interruption. Laptop closed. Next day. ---")

    # Session 2 card
    y -= 28
    card(c, MARGIN, y - 8, CONTENT_W, 70, SURFACE)
    c.setFillColor(CYAN)
    c.rect(MARGIN, y - 8, 4, 70, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN + 18, y + 40, "Session 2")
    c.setFillColor(LIGHT_TEXT)
    c.setFont("Helvetica", 12)
    c.drawString(MARGIN + 18, y + 18, "Claude reads CLAUDE.md. Knows where you left off.")
    c.setFillColor(CYAN)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN + 18, y - 1, "Continue immediately. Zero recovery time.")

    # Bottom result
    card(c, MARGIN, 68, CONTENT_W, 48, SURFACE_LIGHT)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(W / 2, 90, "No mental rebuild. No 23-minute recovery tax.")

    branding(c)


def slide_8_gates(c):
    """Step 5: Evidence Gates - gate cards with status indicators."""
    bg(c)
    slide_num(c, 8, TOTAL)
    accent_dots(c)

    label(c, "STEP 5 OF 5", H - 80)
    heading(c, ["Ship with Evidence Gates"], H - 120, size=28)

    y_sub = H - 185
    c.setFillColor(LIGHT_TEXT)
    c.setFont("Helvetica", 13)
    c.drawString(MARGIN, y_sub, "Do not trust 'should work.' Require proof:")

    gates = [
        ("Gate 1", "Build output passes", "exit code 0", GREEN),
        ("Gate 2", "Type checking clean", "0 errors", CYAN),
        ("Gate 3", "All tests pass", "green suite", PRIMARY),
    ]

    y = H - 235
    for gate_label, title, detail, accent in gates:
        card(c, MARGIN, y - 6, CONTENT_W, 55, SURFACE)
        c.setFillColor(accent)
        c.rect(MARGIN, y - 6, 4, 55, fill=1, stroke=0)
        # Gate label
        c.setFillColor(accent)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(MARGIN + 18, y + 30, gate_label)
        # Title
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(MARGIN + 18, y + 12, title)
        # Detail
        c.setFillColor(LIGHT_TEXT)
        c.setFont("Helvetica", 11)
        c.drawString(MARGIN + 18, y - 4, detail)

        y -= 68

    # Bottom insight
    card(c, MARGIN, 68, CONTENT_W, 55, SURFACE_LIGHT)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN + 14, 103, "WHY THIS MATTERS")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 12)
    c.drawString(MARGIN + 14, 86, "Evidence removes decision paralysis. You do not")
    c.drawString(MARGIN + 14, 72, "'believe' it works. You SEE it.")

    branding(c)


def slide_9_results(c):
    bg(c)
    slide_num(c, 9, TOTAL)
    accent_dots(c)

    label(c, "REAL RESULTS", H - 80)
    heading(c, ["From 0 Shipped Features", "to 3 in 3 Months"], H - 120, size=26)

    months = [
        ("Month 1", "Set up CLAUDE.md + ADHD rules", "Shipped: user profile display", GREEN),
        ("Month 2", "Added evidence gates", "Shipped: profile editing + validation", CYAN),
        ("Month 3", "Async checkpoints every 45 min", "Shipped: full API integration (6 hrs)", PRIMARY),
    ]

    y = H - 230
    for month_name, action, result, accent in months:
        card(c, MARGIN, y - 8, CONTENT_W, 68, SURFACE)

        # Accent left bar
        c.setFillColor(accent)
        c.rect(MARGIN, y - 8, 4, 68, fill=1, stroke=0)

        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(MARGIN + 18, y + 40, month_name)

        c.setFillColor(LIGHT_TEXT)
        c.setFont("Helvetica", 11)
        c.drawString(MARGIN + 18, y + 20, action)

        c.setFillColor(accent)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(MARGIN + 18, y + 2, result)

        y -= 82

    # Quote
    card(c, MARGIN, 72, CONTENT_W, 48, SURFACE_LIGHT)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Oblique", 11)
    c.drawCentredString(W / 2, 92, '"I stopped fighting my brain and started building systems for it."')

    branding(c)


def slide_10_cta(c):
    bg(c, PRIMARY)
    slide_num(c, 10, TOTAL, light=True)

    # Decorative
    c.setFillColor(HexColor("#0d4fb3"))
    c.circle(W + 40, H + 40, 180, fill=1, stroke=0)
    c.circle(-30, -30, 120, fill=1, stroke=0)

    y = H - 100
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 36)
    c.drawString(MARGIN, y, "Start Building")
    y -= 48
    c.drawString(MARGIN, y, "Systems, Not")
    y -= 48
    c.drawString(MARGIN, y, "Fighting Your Brain")

    y -= 45
    steps = [
        "1.  Create CLAUDE.md in your project root",
        "2.  Define YOUR brain rules (not generic ones)",
        "3.  Let Claude decompose your first task",
        "4.  Pick ONE item and start there",
        "5.  Ship with evidence gates, not hope",
    ]
    c.setFillColor(HexColor("#c4dafb"))
    c.setFont("Helvetica", 14)
    for step in steps:
        c.drawString(MARGIN, y, step)
        y -= 26

    # CTA box
    card(c, MARGIN, 80, CONTENT_W, 70, WHITE)
    c.setFillColor(PRIMARY)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(W / 2, 128, "Full guide with templates:")
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(W / 2, 102, "chudi.dev/blog/claude-code-adhd-workflows")

    branding(c, light=True)


def main():
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / "carousel_claude_code_adhd_workflows.pdf"

    c = canvas.Canvas(str(output_path), pagesize=(W, H))

    # Slide 1: Cover
    slide_1_cover(c)
    c.showPage()

    # Slide 2: Problem
    slide_2_problem(c)
    c.showPage()

    # Slide 3: Solution
    slide_3_solution(c)
    c.showPage()

    # Slide 4: Step 1
    slide_step(c, 1, 4, "Start with CLAUDE.md", [
        ("Before writing code, create an external brain:", "body"),
        ("", "gap"),
        ("# Context: What are we building?", "code"),
        ("# Rules: What helps YOUR brain?", "code"),
        ("# Learnings: What have we discovered?", "code"),
        ("# Checkpoint: Where to resume?", "code"),
        ("", "gap"),
        ("Claude reads this file automatically.", "body"),
        ("You never re-explain your project again.", "body"),
    ], tip="Put CLAUDE.md in your project root. It travels with your repo.")
    c.showPage()

    # Slide 5: Step 2 (custom card layout)
    slide_5_rules(c)
    c.showPage()

    # Slide 6: Step 3 (custom layout)
    slide_6_decompose(c)
    c.showPage()

    # Slide 7: Step 4 (custom card layout)
    slide_7_context(c)
    c.showPage()

    # Slide 8: Step 5 (custom card layout)
    slide_8_gates(c)
    c.showPage()

    # Slide 9: Results
    slide_9_results(c)
    c.showPage()

    # Slide 10: CTA
    slide_10_cta(c)
    c.showPage()

    c.save()
    print(f"Carousel PDF created: {output_path}")
    print(f"Total slides: 10")
    print(f"Size: {output_path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
