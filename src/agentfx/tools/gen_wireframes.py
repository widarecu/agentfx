from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

OUT_DIR = Path(__file__).resolve().parents[3] / "planning" / "wireframes"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Try to load a system font; fall back to default
try:
    FONT_TITLE = ImageFont.truetype("Arial.ttf", 48)
    FONT_SUB = ImageFont.truetype("Arial.ttf", 28)
    FONT_BODY = ImageFont.truetype("Arial.ttf", 22)
except Exception:
    FONT_TITLE = ImageFont.load_default()
    FONT_SUB = ImageFont.load_default()
    FONT_BODY = ImageFont.load_default()

TS = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")


def draw_header(draw: ImageDraw.Draw, w: int, text: str):
    draw.rectangle([0, 0, w, 100], fill=(245, 246, 250))
    draw.text((30, 30), text, font=FONT_TITLE, fill=(20, 20, 20))
    draw.text((w - 520, 36), f"Last updated: {TS}", font=FONT_SUB, fill=(80, 80, 80))


def draw_badge(
    draw: ImageDraw.Draw, xy, text: str, fill=(230, 240, 255), outline=(120, 160, 220)
):
    x, y, w, h = xy
    draw.rounded_rectangle(
        [x, y, x + w, y + h], radius=10, fill=fill, outline=outline, width=2
    )
    draw.text((x + 16, y + 10), text, font=FONT_BODY, fill=(30, 50, 120))


def dashboard_wireframe(path: Path, size=(2560, 1440)):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    W, H = size

    # Header
    draw_header(draw, W, "AgentFX Portal — Dashboard (Runs)")

    # Filters/Search row
    y0 = 120
    draw.text((30, y0), "Filters:", font=FONT_SUB, fill=(40, 40, 40))
    draw_badge(draw, (140, y0 - 6, 160, 40), "env: paper")
    draw_badge(draw, (320, y0 - 6, 160, 40), "status: enabled")
    draw_badge(draw, (500, y0 - 6, 160, 40), "pair: ALL")
    draw.rounded_rectangle(
        [700, y0 - 6, 1200, y0 + 34], radius=8, outline=(200, 200, 200), width=2
    )
    draw.text((720, y0), "Search strategies...", font=FONT_BODY, fill=(160, 160, 160))

    # Table header
    y = 200
    x_cols = [30, 520, 680, 820, 980, 1160, 1300, 1420, 1540, 1700, 1920]
    headers = [
        "Strategy (name)",
        "Pair",
        "Env",
        "Status",
        "PnL (Daily)",
        "PnL (Cum)",
        "Sharpe",
        "MDD",
        "Hit%",
        "Turnover",
        "Updated",
    ]
    for i, htxt in enumerate(headers):
        draw.text((x_cols[i], y), htxt, font=FONT_SUB, fill=(60, 60, 60))
    y += 20
    draw.line([30, y + 20, W - 30, y + 20], fill=(220, 220, 220), width=2)

    # Sample rows
    row_h = 70
    rows = [
        [
            "MA Crossover (AUDUSD)",
            "AUDUSD",
            "paper",
            "enabled",
            "-12.34",
            "-532.10",
            "-0.42",
            "-0.31",
            "0.47",
            "320",
            "2025-11-18T01:02:03Z",
        ],
        [
            "RSI MeanRev (EURUSD)",
            "EURUSD",
            "paper",
            "enabled",
            "+4.20",
            "-120.50",
            "-0.15",
            "-0.28",
            "0.49",
            "280",
            "2025-11-18T00:55:11Z",
        ],
    ]
    y += 40
    for r in rows:
        for i, cell in enumerate(r):
            draw.text((x_cols[i], y), str(cell), font=FONT_BODY, fill=(30, 30, 30))
        # view button
        draw_badge(
            draw,
            (W - 140, y - 8, 100, 40),
            "View",
            fill=(235, 255, 235),
            outline=(120, 180, 120),
        )
        y += row_h
        draw.line([30, y, W - 30, y], fill=(240, 240, 240), width=1)

    # Footer notes
    draw.text(
        (30, H - 80),
        "Sort on columns. Pagination: 50 per page. Auto-refresh 60s.",
        font=FONT_BODY,
        fill=(100, 100, 100),
    )

    img.save(path)


def login_wireframe(path: Path, size=(1920, 1080)):
    img = Image.new("RGB", size, (245, 246, 250))
    draw = ImageDraw.Draw(img)
    W, H = size

    # Card
    card_w, card_h = 640, 520
    x0 = (W - card_w) // 2
    y0 = (H - card_h) // 2
    draw.rounded_rectangle(
        [x0, y0, x0 + card_w, y0 + card_h],
        radius=20,
        fill=(255, 255, 255),
        outline=(210, 210, 210),
        width=2,
    )
    draw.text(
        (x0 + 40, y0 + 30), "AgentFX Portal — Login", font=FONT_TITLE, fill=(30, 30, 30)
    )

    # Email field
    draw.text((x0 + 40, y0 + 140), "Email", font=FONT_SUB, fill=(60, 60, 60))
    draw.rounded_rectangle(
        [x0 + 40, y0 + 180, x0 + card_w - 40, y0 + 220],
        radius=8,
        outline=(200, 200, 200),
        width=2,
    )
    draw.text(
        (x0 + 52, y0 + 186), "you@example.com", font=FONT_BODY, fill=(160, 160, 160)
    )

    # Password field
    draw.text((x0 + 40, y0 + 250), "Password", font=FONT_SUB, fill=(60, 60, 60))
    draw.rounded_rectangle(
        [x0 + 40, y0 + 290, x0 + card_w - 40, y0 + 330],
        radius=8,
        outline=(200, 200, 200),
        width=2,
    )
    draw.text((x0 + 52, y0 + 296), "••••••••", font=FONT_BODY, fill=(160, 160, 160))

    # Remember + Forgot
    draw.text((x0 + 40, y0 + 350), "[ ] Remember me", font=FONT_BODY, fill=(80, 80, 80))
    draw.text(
        (x0 + card_w - 220, y0 + 350),
        "Forgot password?",
        font=FONT_BODY,
        fill=(60, 120, 200),
    )

    # Sign in button
    draw.rounded_rectangle(
        [x0 + 40, y0 + 390, x0 + card_w - 40, y0 + 440], radius=10, fill=(40, 120, 220)
    )
    draw.text((x0 + 260, y0 + 400), "Sign in", font=FONT_SUB, fill=(255, 255, 255))

    # Footer
    draw.text(
        (x0 + 40, y0 + 470),
        "Viewer role only. Session cookie. No secrets in client.",
        font=FONT_BODY,
        fill=(120, 120, 120),
    )

    img.save(path)


def session_expired_wireframe(path: Path, size=(1920, 1080)):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    W, H = size

    # Dim background
    draw.rectangle([0, 0, W, H], fill=(245, 246, 250))
    draw_header(draw, W, "AgentFX Portal")

    # Modal
    modal_w, modal_h = 720, 360
    x0 = (W - modal_w) // 2
    y0 = (H - modal_h) // 2
    draw.rounded_rectangle(
        [x0, y0, x0 + modal_w, y0 + modal_h],
        radius=16,
        fill=(255, 255, 255),
        outline=(210, 210, 210),
        width=2,
    )
    draw.text((x0 + 40, y0 + 30), "Session expired", font=FONT_TITLE, fill=(30, 30, 30))
    draw.text(
        (x0 + 40, y0 + 110),
        "For your security your session has expired.",
        font=FONT_SUB,
        fill=(80, 80, 80),
    )
    draw.text(
        (x0 + 40, y0 + 160),
        "Please re-authenticate to continue.",
        font=FONT_SUB,
        fill=(80, 80, 80),
    )

    # Buttons
    draw.rounded_rectangle(
        [x0 + modal_w - 320, y0 + modal_h - 90, x0 + modal_w - 180, y0 + modal_h - 40],
        radius=10,
        outline=(200, 200, 200),
        width=2,
    )
    draw.text(
        (x0 + modal_w - 300, y0 + modal_h - 80),
        "Cancel",
        font=FONT_SUB,
        fill=(60, 60, 60),
    )
    draw.rounded_rectangle(
        [x0 + modal_w - 160, y0 + modal_h - 90, x0 + modal_w - 40, y0 + modal_h - 40],
        radius=10,
        fill=(40, 120, 220),
    )
    draw.text(
        (x0 + modal_w - 140, y0 + modal_h - 80),
        "Sign in",
        font=FONT_SUB,
        fill=(255, 255, 255),
    )

    img.save(path)


def strategy_detail_wireframe(path: Path, size=(2560, 1440)):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    W, H = size

    # Header
    draw_header(draw, W, "MA Crossover — AUDUSD — paper")

    # Controls
    y0 = 120
    draw.text((30, y0), "TF:", font=FONT_SUB, fill=(40, 40, 40))
    for i, tf in enumerate(["M1", "M5*", "M15", "H1"]):
        draw_badge(draw, (90 + i * 120, y0 - 6, 100, 40), tf)
    draw.text((600, y0), "Range:", font=FONT_SUB, fill=(40, 40, 40))
    for i, tf in enumerate(["1d*", "7d", "30d"]):
        draw_badge(draw, (700 + i * 120, y0 - 6, 100, 40), tf)
    draw.text((1100, y0), "Overlays:", font=FONT_SUB, fill=(40, 40, 40))
    for i, ov in enumerate(["[x] MA", "[x] RSI", "[ ] Donchian", "[ ] Decisions"]):
        draw_badge(draw, (1220 + i * 200, y0 - 6, 180, 40), ov)

    # Main chart area
    chart_top = 200
    chart_h = 700
    chart_right = W - 60
    draw.rounded_rectangle(
        [30, chart_top, chart_right, chart_top + chart_h],
        radius=12,
        outline=(200, 200, 200),
        width=3,
    )
    draw.text(
        (40, chart_top + 10),
        "Candlesticks + MA overlays + Position bands",
        font=FONT_SUB,
        fill=(80, 80, 80),
    )

    # RSI subpanel
    rsi_top = chart_top + chart_h + 20
    rsi_h = 220
    draw.rounded_rectangle(
        [30, rsi_top, chart_right, rsi_top + rsi_h],
        radius=12,
        outline=(200, 200, 200),
        width=3,
    )
    draw.text((40, rsi_top + 10), "RSI (30/70 bands)", font=FONT_SUB, fill=(80, 80, 80))

    # Trades table
    table_top = rsi_top + rsi_h + 20
    table_h = 300
    draw.rounded_rectangle(
        [30, table_top, chart_right, table_top + table_h],
        radius=12,
        outline=(200, 200, 200),
        width=3,
    )
    headers = [
        "Trade ID",
        "Side",
        "Qty",
        "Entry ts",
        "Entry",
        "Exit ts",
        "Exit",
        "Net PnL",
        "Return %",
        "Dur.",
        "MAE pips",
        "MFE pips",
    ]
    x_cols = [40, 260, 340, 420, 700, 820, 1000, 1120, 1260, 1380, 1480, 1600]
    draw.text((40, table_top + 10), "Trades", font=FONT_SUB, fill=(60, 60, 60))
    y = table_top + 50
    for i, htxt in enumerate(headers):
        draw.text((x_cols[i], y), htxt, font=FONT_BODY, fill=(60, 60, 60))
    y += 20
    draw.line([40, y + 20, chart_right - 20, y + 20], fill=(230, 230, 230), width=2)
    y += 40
    sample = [
        [
            "t_20251117_001",
            "long",
            "1.0",
            "2025-11-17T02:10Z",
            "0.6540",
            "2025-11-17T03:05Z",
            "0.6544",
            "38.5",
            "0.0612",
            "55m",
            "-6.0",
            "12.0",
        ],
    ]
    for r in sample:
        for i, cell in enumerate(r):
            draw.text((x_cols[i], y), str(cell), font=FONT_BODY, fill=(30, 30, 30))

    # Recent orders
    orders_top = table_top
    orders_left = chart_right + 20
    # Right-side panel placeholder if needed in future

    img.save(path)


if __name__ == "__main__":
    dashboard_wireframe(OUT_DIR / "portal_dashboard.png")
    strategy_detail_wireframe(OUT_DIR / "portal_strategy_detail.png")
    login_wireframe(OUT_DIR / "portal_login.png")
    session_expired_wireframe(OUT_DIR / "portal_session_expired.png")
    print(f"Saved wireframes to {OUT_DIR}")
