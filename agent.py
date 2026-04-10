import os
import io
import json
import asyncio
import datetime
import requests
from PIL import Image, ImageDraw, ImageFont
import openai
from anthropic import Anthropic
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bidi.algorithm import get_display
import arabic_reshaper

# ── ENV ──────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
META_TOKEN        = os.environ.get("META_TOKEN", "")
AD_ACCOUNT_ID     = os.environ.get("AD_ACCOUNT_ID", "act_2783197798689936")
IG_PAGE_ID        = os.environ.get("IG_PAGE_ID", "")

openai.api_key = OPENAI_API_KEY
claude = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── COLORS & BRAND ────────────────────────────────────────────────────────────
NEWSITE_PURPLE = (107, 70, 193)
NEWSITE_WHITE  = (255, 255, 255)
NEWSITE_DARK   = (20, 20, 30)
NEWSITE_GRAD1  = (107, 70, 193)
NEWSITE_GRAD2  = (60, 130, 246)

# ── TOOLS ─────────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "generate_image_dalle",
        "description": "יוצר תמונה/גרפיקה מקורית עם DALL-E 3 לפי תיאור. מחזיר URL של התמונה.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "תיאור מפורט של התמונה באנגלית"
                },
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x1792", "1792x1024"],
                    "description": "גודל: 1024x1024=ריבוע, 1024x1792=סטורי/רילס, 1792x1024=רוחבי"
                },
                "style": {
                    "type": "string",
                    "enum": ["vivid", "natural"],
                    "description": "vivid=צבעוני ודרמטי, natural=ריאליסטי"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "create_carousel",
        "description": "יוצר קרוסלה לאינסטגרם עם מספר שקופיות. כל שקופית עם כותרת + טקסט + תמונת DALL-E.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slides": {
                    "type": "array",
                    "description": "רשימת שקופיות",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title":   {"type": "string", "description": "כותרת השקופית (עברית/ערבית/אנגלית)"},
                            "text":    {"type": "string", "description": "טקסט הסבר קצר"},
                            "image_prompt": {"type": "string", "description": "תיאור תמונת רקע באנגלית"}
                        },
                        "required": ["title", "text", "image_prompt"]
                    }
                },
                "topic": {
                    "type": "string",
                    "description": "נושא הקרוסלה (למטא-נתונים)"
                }
            },
            "required": ["slides", "topic"]
        }
    },
    {
        "name": "create_ad_graphic",
        "description": "יוצר מודעה גרפית מוכנה לקמפיין פייסבוק/אינסטגרם עם לוגו NEWSITE, CTA וטקסט.",
        "input_schema": {
            "type": "object",
            "properties": {
                "headline":    {"type": "string", "description": "כותרת ראשית של המודעה"},
                "subtext":     {"type": "string", "description": "טקסט משני"},
                "cta":         {"type": "string", "description": "כפתור CTA: 'לפרטים', 'הזמן עכשיו', 'קבל הצעה' וכו'"},
                "bg_prompt":   {"type": "string", "description": "תיאור הרקע (DALL-E) באנגלית"},
                "format":      {
                    "type": "string",
                    "enum": ["square", "story", "landscape"],
                    "description": "square=1080x1080, story=1080x1920, landscape=1200x628"
                }
            },
            "required": ["headline", "subtext", "cta", "bg_prompt"]
        }
    },
    {
        "name": "write_copy",
        "description": "כותב קופי שיווקי: פוסט לאינסטגרם, מודעה, תסריט לרילס, כיתוב לתמונה.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["instagram_post", "ad_copy", "reels_script", "carousel_copy", "story_copy"],
                    "description": "סוג הקופי"
                },
                "topic":    {"type": "string", "description": "נושא התוכן"},
                "language": {
                    "type": "string",
                    "enum": ["hebrew", "arabic", "english", "hebrew+arabic"],
                    "description": "שפת הקופי"
                },
                "tone": {
                    "type": "string",
                    "enum": ["professional", "friendly", "urgent", "inspirational"],
                    "description": "טון הדיבור"
                },
                "target_audience": {"type": "string", "description": "קהל היעד"}
            },
            "required": ["type", "topic", "language"]
        }
    },
    {
        "name": "send_to_telegram",
        "description": "שולח תמונה/גרפיקה עם הסבר לטלגרם.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "נתיב לקובץ תמונה"},
                "image_url":  {"type": "string", "description": "URL של תמונה (חלופה לpath)"},
                "caption":    {"type": "string", "description": "הסבר/כיתוב לתמונה"}
            }
        }
    }
]

SYSTEM = """אתה Creative Agent של NEWSITE — חברה לבניית אתרים במנוי חודשי לעסקים קטנים.

**תפקידך:**
- יצירת תוכן גרפי: קרוסלות, מודעות, גרפיקות
- כתיבת קופי שיווקי בעברית, ערבית ואנגלית
- יצירת תמונות מקוריות עם DALL-E 3

**קהל היעד של NEWSITE:**
- עסקים קטנים ובינוניים בישראל (עברית + ערבית)
- בעלי עסקים שרוצים אתר מקצועי בלי לשלם הרבה
- כאב: אתרים יקרים, מסובכים, בלי תמיכה

**מסרים מרכזיים:**
- "אתר מקצועי ב-299₪ לחודש"
- "בלי חוזים, בלי הפתעות"
- "תמיכה אישית בעברית ובערבית"
- "כולל: עיצוב + דומיין + אחסון + SEO"

**סגנון עיצובי:**
- צבעים: סגול #6B46C1, לבן, כחול-סגול מדרגתי
- פונט: מינימליסטי, מודרני
- תמונות: מקצועיות, בהירות, עם אנשים עסקיים

**כשמקבל בקשה:**
1. הבן מה צריך
2. השתמש בכלים המתאימים
3. שלח לטלגרם
4. הסבר מה יצרת ולמה

תמיד חשוב: מה הCTA? מה ה-Hook? מה גורם לעצירת הגלילה?"""

# ── TOOL FUNCTIONS ─────────────────────────────────────────────────────────────

def generate_image_dalle(prompt: str, size: str = "1024x1024", style: str = "vivid") -> dict:
    """יוצר תמונה עם DALL-E 3"""
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
            style=style,
            quality="hd",
            n=1
        )
        url = response.data[0].url
        revised = response.data[0].revised_prompt
        return {"url": url, "revised_prompt": revised, "success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def download_image(url: str) -> Image.Image:
    """מוריד תמונה מURL"""
    resp = requests.get(url, timeout=30)
    return Image.open(io.BytesIO(resp.content)).convert("RGBA")


def create_gradient_bg(width: int, height: int) -> Image.Image:
    """יוצר רקע גרדיאנט NEWSITE"""
    img = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / height
        r = int(NEWSITE_GRAD1[0] + (NEWSITE_GRAD2[0] - NEWSITE_GRAD1[0]) * ratio)
        g = int(NEWSITE_GRAD1[1] + (NEWSITE_GRAD2[1] - NEWSITE_GRAD1[1]) * ratio)
        b = int(NEWSITE_GRAD1[2] + (NEWSITE_GRAD2[2] - NEWSITE_GRAD1[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))
    return img


# ── FONT SETUP ────────────────────────────────────────────────────────────────
FONT_PATH = "/tmp/NotoSansHebrew.ttf"
FONT_URL  = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansHebrew/NotoSansHebrew-Regular.ttf"

def ensure_font():
    if not os.path.exists(FONT_PATH):
        try:
            r = requests.get(FONT_URL, timeout=30)
            with open(FONT_PATH, "wb") as f:
                f.write(r.content)
            print("✅ פונט הורד")
        except Exception as e:
            print(f"⚠️ לא הצלחתי להוריד פונט: {e}")

def get_font(size: int) -> ImageFont.FreeTypeFont:
    paths = [
        FONT_PATH,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except:
            continue
    return ImageFont.load_default()

def fix_rtl(text: str) -> str:
    """תיקון כיוון RTL לעברית/ערבית"""
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except:
        return text

def draw_text_block(draw: ImageDraw.Draw, text: str, y_center: int,
                     img_width: int, font_size: int,
                     color: tuple = (255, 255, 255),
                     max_chars: int = 22) -> int:
    """מצייר בלוק טקסט מרוכז עם wrap — מחזיר Y הסופי"""
    font = get_font(font_size)
    # חלוקה לשורות לפי אורך
    words   = text.split()
    lines   = []
    current = []
    for w in words:
        current.append(w)
        if len(" ".join(current)) > max_chars:
            if len(current) > 1:
                current.pop()
                lines.append(" ".join(current))
                current = [w]
    if current:
        lines.append(" ".join(current))

    line_h = font_size + 10
    total_h = len(lines) * line_h
    y = y_center - total_h // 2

    for line in lines:
        rtl_line = fix_rtl(line)
        bbox = draw.textbbox((0, 0), rtl_line, font=font)
        w = bbox[2] - bbox[0]
        x = (img_width - w) // 2
        # צל
        draw.text((x+2, y+2), rtl_line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), rtl_line, font=font, fill=color)
        y += line_h

    return y


def create_carousel(slides: list, topic: str) -> list:
    """יוצר קרוסלה לאינסטגרם — עיצוב מקצועי"""
    ensure_font()
    output_paths = []
    W, H = 1080, 1080

    for i, slide in enumerate(slides):
        # ── רקע: DALL-E או גרדיאנט ──
        result = generate_image_dalle(
            prompt=f"Professional minimalist business photo, no text, suitable for overlay: {slide['image_prompt']}. High quality, clean composition.",
            size="1024x1024",
            style="vivid"
        )
        if result["success"]:
            bg = download_image(result["url"]).resize((W, H), Image.LANCZOS).convert("RGBA")
        else:
            bg = create_gradient_bg(W, H)

        # ── overlay כהה (גרדיאנט מלמטה) ──
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for row in range(H):
            alpha = int(80 + (row / H) * 140)
            od.line([(0, row), (W, row)], fill=(10, 5, 30, alpha))
        bg = Image.alpha_composite(bg, overlay)

        draw = ImageDraw.Draw(bg)

        # ── פס עליון: מספר שקופית + שם מותג ──
        draw.rectangle([(0, 0), (W, 80)], fill=(107, 70, 193, 200))
        f_small = get_font(30)
        draw.text((30, 22), fix_rtl(f"NEWSITE"), font=f_small, fill=(255,255,255))
        num_text = f"{i+1} / {len(slides)}"
        nb = draw.textbbox((0,0), num_text, font=f_small)
        draw.text((W - (nb[2]-nb[0]) - 30, 22), num_text, font=f_small, fill=(220,220,220))

        # ── כותרת ראשית ──
        draw_text_block(draw, slide["title"], H // 2 - 60, W,
                        font_size=68, color=(255, 255, 255), max_chars=18)

        # ── קו מפריד ──
        draw.rectangle([(W//2 - 60, H//2 + 50), (W//2 + 60, H//2 + 56)],
                        fill=(107, 70, 193))

        # ── טקסט משני ──
        draw_text_block(draw, slide["text"], H // 2 + 120, W,
                        font_size=38, color=(220, 220, 240), max_chars=28)

        # ── פס תחתון ──
        draw.rectangle([(0, H - 90), (W, H)], fill=(15, 10, 40, 230))
        f_logo = get_font(32)
        logo = fix_rtl("newsite.co.il")
        lb = draw.textbbox((0,0), logo, font=f_logo)
        draw.text(((W - (lb[2]-lb[0])) // 2, H - 65), logo,
                   font=f_logo, fill=(167, 139, 250))

        # ── שמירה ──
        path = f"/tmp/slide_{topic}_{i+1}.png"
        bg.convert("RGB").save(path, "PNG", quality=95)
        output_paths.append(path)

    return output_paths


def create_ad_graphic(headline: str, subtext: str, cta: str,
                       bg_prompt: str, format: str = "square") -> str:
    """יוצר מודעה גרפית"""
    sizes = {
        "square":    (1080, 1080),
        "story":     (1080, 1920),
        "landscape": (1200, 628)
    }
    width, height = sizes.get(format, (1080, 1080))

    # יצירת רקע עם DALL-E
    result = generate_image_dalle(
        prompt=f"Professional clean background for ad: {bg_prompt}. Minimalist, suitable for text overlay, no text, business oriented.",
        size="1024x1024" if format == "square" else ("1024x1792" if format == "story" else "1792x1024"),
        style="vivid"
    )

    if result["success"]:
        bg_img = download_image(result["url"])
        bg_img = bg_img.resize((width, height), Image.LANCZOS)
    else:
        bg_img = create_gradient_bg(width, height)

    # overlay
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 120))
    bg_img = Image.alpha_composite(bg_img.convert("RGBA"), overlay)

    # Headline
    title_y = int(height * 0.3)
    bg_img = add_text_to_image(bg_img, headline, (0, title_y),
                                font_size=72 if format == "square" else 80,
                                color=NEWSITE_WHITE)

    # Subtext
    sub_y = int(height * 0.52)
    bg_img = add_text_to_image(bg_img, subtext, (0, sub_y),
                                font_size=40, color=(220, 220, 255))

    # CTA Button
    btn_y = int(height * 0.7)
    btn_w, btn_h = 400, 80
    btn_x = (width - btn_w) // 2
    draw = ImageDraw.Draw(bg_img)
    draw.rounded_rectangle([(btn_x, btn_y), (btn_x + btn_w, btn_y + btn_h)],
                             radius=40, fill=NEWSITE_PURPLE)
    bg_img = add_text_to_image(bg_img, cta, (0, btn_y + 20),
                                font_size=36, color=NEWSITE_WHITE)

    # לוגו
    logo_y = int(height * 0.88)
    bg_img = add_text_to_image(bg_img, "NEWSITE.CO.IL", (0, logo_y),
                                font_size=28, color=(200, 200, 200))

    # שמירה
    path = f"/tmp/ad_{format}_{datetime.datetime.now().strftime('%H%M%S')}.png"
    bg_img.convert("RGB").save(path, "PNG", quality=95)
    return path


def write_copy(type: str, topic: str, language: str,
               tone: str = "professional", target_audience: str = "עסקים קטנים") -> str:
    """כותב קופי שיווקי"""

    lang_map = {
        "hebrew": "עברית",
        "arabic": "ערבית",
        "english": "English",
        "hebrew+arabic": "עברית וערבית (גרסה כפולה)"
    }

    type_map = {
        "instagram_post": "פוסט לאינסטגרם (300-500 מילים עם האשטגים)",
        "ad_copy":        "טקסט מודעה (כותרת + 2 שורות + CTA)",
        "reels_script":   "תסריט לרילס 30 שניות (תיאור ויזואל + טקסט על מסך + VO)",
        "carousel_copy":  "טקסט לקרוסלה (5-7 שקופיות, כל אחת כותרת + 2 שורות)",
        "story_copy":     "סטורי (קצר, דרמטי, עם poll/question)"
    }

    prompt = f"""כתוב {type_map.get(type, type)} עבור NEWSITE בנושא: {topic}

שפה: {lang_map.get(language, language)}
טון: {tone}
קהל יעד: {target_audience}

על NEWSITE:
- בניית אתרים מקצועיים במנוי 299₪/חודש
- כולל: עיצוב + דומיין + אחסון + SEO + תמיכה
- ללא חוזים ארוכים
- שירות בעברית ובערבית

הקופי צריך:
✓ Hook חזק בשורה הראשונה שעוצר גלילה
✓ ערך ברור לקורא
✓ CTA ספציפי
✓ מתאים לפלטפורמה"""

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


async def send_to_telegram_func(image_path: str = None, image_url: str = None,
                                 caption: str = "") -> dict:
    """שולח תמונה לטלגרם"""
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                await bot.send_photo(chat_id=CHAT_ID, photo=f, caption=caption[:1024])
        elif image_url:
            await bot.send_photo(chat_id=CHAT_ID, photo=image_url, caption=caption[:1024])
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── AGENT LOOP ─────────────────────────────────────────────────────────────────

async def run_agent(user_message: str) -> str:
    """הרץ Creative Agent עם tool use"""
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages
        )

        if response.stop_reason == "end_turn":
            # מצא את הtexts
            texts = [b.text for b in response.content if hasattr(b, "text")]
            return "\n".join(texts) if texts else "✅ בוצע!"

        if response.stop_reason != "tool_use":
            break

        # עיבוד כלים
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name  = block.name
            tool_input = block.input
            result     = None

            print(f"🔧 {tool_name}: {json.dumps(tool_input, ensure_ascii=False)[:100]}")

            if tool_name == "generate_image_dalle":
                result = generate_image_dalle(**tool_input)

            elif tool_name == "create_carousel":
                paths = create_carousel(tool_input["slides"], tool_input["topic"])
                result = {"paths": paths, "count": len(paths), "success": True}
                # שלח לטלגרם אוטומטית
                for i, path in enumerate(paths):
                    await send_to_telegram_func(
                        image_path=path,
                        caption=f"🎨 שקופית {i+1}/{len(paths)} | {tool_input['topic']}"
                    )

            elif tool_name == "create_ad_graphic":
                path = create_ad_graphic(**tool_input)
                result = {"path": path, "success": True}
                await send_to_telegram_func(
                    image_path=path,
                    caption=f"📢 מודעה: {tool_input['headline']}"
                )

            elif tool_name == "write_copy":
                copy_text = write_copy(**tool_input)
                result = {"copy": copy_text, "success": True}

            elif tool_name == "send_to_telegram":
                result = await send_to_telegram_func(**tool_input)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False)
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    return "✅ בוצע"


# ── TELEGRAM HANDLER ───────────────────────────────────────────────────────────

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """מטפל בהודעות טלגרם"""
    if str(update.effective_chat.id) != str(CHAT_ID):
        return

    text = update.message.text or ""
    bot  = context.bot

    await bot.send_message(chat_id=CHAT_ID, text="🎨 יוצר תוכן... רגע!")

    try:
        result = await run_agent(text)
        await bot.send_message(chat_id=CHAT_ID, text=result[:4096])
    except Exception as e:
        await bot.send_message(chat_id=CHAT_ID, text=f"❌ שגיאה: {e}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """🎨 *Creative Agent — NEWSITE*

*פקודות:*
• קרוסלה על [נושא] — יוצר 5 שקופיות
• מודעה [כותרת] — מודעת פייסבוק/אינסטגרם
• תמונה [תיאור] — תמונה עם DALL-E 3
• קופי [סוג] [נושא] — טקסט שיווקי

*דוגמאות:*
• `קרוסלה על למה כל עסק צריך אתר`
• `מודעה אתר מקצועי ב299 לחודש`
• `קופי פוסט בניית אתרים עברית`
• `תמונה office with modern website on screen`
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


# ── SCHEDULED CONTENT ──────────────────────────────────────────────────────────

async def weekly_content_ideas():
    """כל ראשון ב-9:00 — רעיונות תוכן לשבוע"""
    bot = Bot(token=TELEGRAM_TOKEN)

    prompt = """צור תוכנית תוכן שבועית ל-NEWSITE לשבוע הקרוב:

    כלול:
    1. 3 רעיונות לפוסטים לאינסטגרם
    2. 2 רעיונות לרילס
    3. 1 קרוסלה שאפשר ליצור
    4. טרנד שכדאי לנצל השבוע

    פורמט נקי עם אמוג'י."""

    result = await run_agent(prompt)
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"📅 *תוכנית תוכן שבועית:*\n\n{result}",
        parse_mode="Markdown"
    )


# ── MAIN ──────────────────────────────────────────────────────────────────────

async def post_init(application):
    scheduler = AsyncIOScheduler(timezone="Asia/Jerusalem")
    scheduler.add_job(weekly_content_ideas, "cron", day_of_week="mon", hour=9, minute=0)
    scheduler.start()
    print("✅ Scheduler פועל")


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("✅ Creative Agent פועל...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
