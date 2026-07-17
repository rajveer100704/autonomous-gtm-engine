"""
Demo GIF Generator — creates docs/demo.gif programmatically using Pillow.

The GIF shows the campaign lifecycle in 5 animated phases:
  Phase 1: Campaign started (amber pulse)
  Phase 2: Research in progress (bar fill animation)
  Phase 3: Quality Review passed (teal checkmark)
  Phase 4: Email + LinkedIn sent (dual channel icons)
  Phase 5: Dashboard updated (KPI numbers counting up)

Output: docs/demo.gif (480x270px, 15fps, ~1.5MB, loops)

Run:
    python gtm_engine/docs/gen_demo_gif.py
"""
from __future__ import annotations

import math
import os
import sys

# ── Dependencies check ────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    import imageio
    import numpy as np
except ImportError:
    print("Install: pip install pillow imageio numpy")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────
W, H = 480, 270
FPS = 15
BG = (14, 17, 20)          # #0E1114
CARD = (24, 28, 33)        # #181C21
BORDER = (37, 43, 51)      # #252B33
AMBER = (240, 175, 84)     # #F0AF54
TEAL = (48, 191, 153)      # #30BF99
DIM = (135, 140, 151)      # #878C97
WHITE = (230, 229, 226)    # #E6E5E2
RED = (192, 92, 78)


def blank(bg=BG) -> Image.Image:
    return Image.new("RGB", (W, H), bg)


def draw_card(d: ImageDraw.ImageDraw, x, y, w, h, color=CARD, border=BORDER):
    d.rectangle([x, y, x+w, y+h], fill=color, outline=border, width=1)


def draw_text(d: ImageDraw.ImageDraw, text: str, pos, color=WHITE, size: int = 12):
    try:
        font = ImageFont.truetype("arial.ttf", size)
    except (IOError, OSError):
        font = ImageFont.load_default()
    d.text(pos, text, fill=color, font=font)


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out(t):
    return 1 - (1 - t) ** 3


def pulse_alpha(t, period=1.0):
    return 0.5 + 0.5 * math.sin(t * 2 * math.pi / period)


# ── Phase renderers ───────────────────────────────────────────────────────

def phase1_frames(n=20) -> list[Image.Image]:
    """Campaign started — animated amber dot + title."""
    frames = []
    for i in range(n):
        img = blank()
        d = ImageDraw.Draw(img)
        t = i / (n - 1)

        # Card
        draw_card(d, 40, 50, W - 80, H - 100)

        # Pulsing amber circle
        pulse = pulse_alpha(t, 1.0)
        r = int(lerp(18, 30, pulse))
        cx, cy = W // 2, H // 2 - 10
        # Glow ring
        glow_c = (int(AMBER[0]*0.3), int(AMBER[1]*0.3), int(AMBER[2]*0.3))
        d.ellipse([cx-r-8, cy-r-8, cx+r+8, cy+r+8], fill=glow_c)
        d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=AMBER)

        draw_text(d, "GTM Engine", (W//2 - 50, cy + r + 14), WHITE, 14)
        draw_text(d, "Campaign started", (W//2 - 60, cy + r + 34), DIM, 11)

        # Amber bar growing
        bar_w = int(lerp(0, 300, ease_out(t)))
        d.rectangle([W//2 - 150, H - 80, W//2 - 150 + bar_w, H - 72], fill=AMBER)
        d.rectangle([W//2 - 150, H - 80, W//2 + 150, H - 72], outline=BORDER, width=1)

        frames.append(img)
    return frames


def phase2_frames(n=25) -> list[Image.Image]:
    """Research in progress — agent bar chart filling."""
    agents = [
        ("Apollo", AMBER),
        ("Research", TEAL),
        ("News", TEAL),
        ("Hiring", TEAL),
        ("Tech Stack", AMBER),
    ]
    frames = []
    for i in range(n):
        img = blank()
        d = ImageDraw.Draw(img)

        draw_card(d, 40, 30, W - 80, H - 60)
        draw_text(d, "Company Intelligence", (60, 48), WHITE, 13)
        draw_text(d, "grounding research...", (60, 66), DIM, 10)

        for ai, (name, color) in enumerate(agents):
            y = 90 + ai * 30
            # Progress fills left-to-right with stagger
            agent_t = max(0, min(1, (i / (n - 1) - ai * 0.12) / 0.5))
            bar_fill = int(lerp(0, 220, ease_out(agent_t)))

            draw_text(d, name, (60, y + 2), DIM, 10)
            d.rectangle([150, y, 370, y + 14], fill=(20, 24, 28), outline=BORDER, width=1)
            if bar_fill > 0:
                d.rectangle([150, y, 150 + bar_fill, y + 14], fill=color)
            if agent_t >= 1.0:
                draw_text(d, "✓", (375, y + 1), color, 11)

        frames.append(img)
    return frames


def phase3_frames(n=18) -> list[Image.Image]:
    """Quality Review — teal checkmark reveal."""
    frames = []
    for i in range(n):
        img = blank()
        d = ImageDraw.Draw(img)
        t = ease_out(i / (n - 1))

        draw_card(d, 40, 50, W - 80, H - 100)

        # Teal circle growing
        cx, cy = W // 2, H // 2 - 15
        r = int(lerp(0, 32, t))
        if r > 0:
            d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=TEAL)

        # Checkmark (simplified as text for portability)
        if t > 0.6:
            alpha = min(1.0, (t - 0.6) / 0.4)
            check_color = tuple(int(c * alpha + (1-alpha) * TEAL[j]) for j, c in enumerate(WHITE))
            draw_text(d, "✓", (cx - 8, cy - 12), check_color, 22)

        draw_text(d, "Quality Review", (W//2 - 54, cy + 44), WHITE, 13)
        label = "passed" if t > 0.7 else "reviewing..."
        label_col = TEAL if t > 0.7 else DIM
        draw_text(d, label, (W//2 - 25, cy + 62), label_col, 10)

        frames.append(img)
    return frames


def phase4_frames(n=22) -> list[Image.Image]:
    """Email + LinkedIn sent — dual channel animation."""
    frames = []
    for i in range(n):
        img = blank()
        d = ImageDraw.Draw(img)

        draw_card(d, 40, 30, W - 80, H - 60)
        draw_text(d, "Outreach Executed", (60, 48), WHITE, 13)

        t = i / (n - 1)

        # Email card (slides in from left)
        email_x = int(lerp(-160, 60, ease_out(min(1, t * 2))))
        draw_card(d, email_x, 80, 160, 120, CARD, AMBER if t > 0.4 else BORDER)
        draw_text(d, "✉  Email", (email_x + 14, 94), AMBER if t > 0.4 else DIM, 11)
        draw_text(d, "sent", (email_x + 14, 112), TEAL if t > 0.5 else DIM, 10)
        draw_text(d, "Variant A", (email_x + 14, 130), DIM, 9)
        draw_text(d, "Quality: ✓", (email_x + 14, 148), TEAL, 9)

        # LinkedIn card (slides in from right)
        li_t = max(0, (t - 0.3) * 1.4)
        li_x = int(lerp(W + 20, 260, ease_out(min(1, li_t))))
        draw_card(d, li_x, 80, 160, 120, CARD, TEAL if li_t > 0.5 else BORDER)
        draw_text(d, "in LinkedIn", (li_x + 14, 94), TEAL if li_t > 0.5 else DIM, 11)
        draw_text(d, "connection sent", (li_x + 14, 112), TEAL if li_t > 0.7 else DIM, 9)
        draw_text(d, "Human approval", (li_x + 14, 130), DIM, 9)

        frames.append(img)
    return frames


def phase5_frames(n=25) -> list[Image.Image]:
    """Dashboard updated — KPI counters animating."""
    kpis = [
        ("Leads", 4, AMBER),
        ("Emails", 4, WHITE),
        ("Replies", 1, TEAL),
        ("Meetings", 0, TEAL),
        ("Cost", 0, WHITE),  # special
        ("Health", 0, WHITE),  # special
    ]
    frames = []
    for i in range(n):
        img = blank()
        d = ImageDraw.Draw(img)
        t = ease_out(i / (n - 1))

        draw_card(d, 30, 20, W - 60, 60)
        draw_text(d, "Dashboard  —  pipeline updated", (50, 38), DIM, 10)

        for ki, (label, target, color) in enumerate(kpis):
            kx = 50 + ki * 72
            ky = 100
            draw_card(d, kx - 10, ky - 8, 68, 64)
            draw_text(d, label, (kx, ky), DIM, 9)
            val = int(target * t)
            if ki == 4:
                v_str = f"${val * 0.002:.4f}"
            elif ki == 5:
                v_str = f"{min(val * 12, 90)}/100"
            else:
                v_str = str(val)
            draw_text(d, v_str, (kx, ky + 16), color, 16)

        # Progress bar
        d.rectangle([50, H - 50, W - 50, H - 42], fill=(20, 24, 28), outline=BORDER)
        fill_w = int((W - 100) * t)
        d.rectangle([50, H - 50, 50 + fill_w, H - 42], fill=AMBER)
        draw_text(d, f"{int(t * 100)}% complete", (50, H - 36), DIM, 9)

        frames.append(img)
    return frames


# ── Transition ────────────────────────────────────────────────────────────

def crossfade(f1: Image.Image, f2: Image.Image, steps=6) -> list[Image.Image]:
    frames = []
    for i in range(steps):
        t = i / (steps - 1)
        blended = Image.blend(f1, f2, t)
        frames.append(blended)
    return frames


# ── Main ──────────────────────────────────────────────────────────────────

def generate_demo_gif(output_path: str = "gtm_engine/docs/demo.gif") -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    print("Generating demo GIF frames...")
    p1 = phase1_frames(20)
    p2 = phase2_frames(25)
    p3 = phase3_frames(18)
    p4 = phase4_frames(22)
    p5 = phase5_frames(25)

    all_frames = (
        p1 +
        crossfade(p1[-1], p2[0]) +
        p2 +
        crossfade(p2[-1], p3[0]) +
        p3 +
        crossfade(p3[-1], p4[0]) +
        p4 +
        crossfade(p4[-1], p5[0]) +
        p5
    )

    print(f"Total frames: {len(all_frames)}")

    # Convert PIL Images to numpy for imageio
    np_frames = [np.array(f.convert("RGB")) for f in all_frames]

    imageio.mimsave(
        output_path,
        np_frames,
        fps=FPS,
        loop=0,  # infinite loop
    )

    size_kb = os.path.getsize(output_path) / 1024
    print(f"Saved: {output_path} ({size_kb:.0f} KB, {len(all_frames)} frames @ {FPS}fps)")

    if size_kb > 2048:
        print("⚠  File > 2MB — consider reducing frames or colors for message GIFs")

    return output_path


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "gtm_engine/docs/demo.gif"
    generate_demo_gif(path)
