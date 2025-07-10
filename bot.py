import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta

import dateparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, CallbackContext
)
from apscheduler.schedulers.background import BackgroundScheduler

import config  # loads TELEGRAM_TOKEN

# — Logging setup —
logging.basicConfig(
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# — Asyncio loop reference —
LOOP = asyncio.get_event_loop()

# — Database file (absolute path) —
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tasks.db")

# — Database Helpers —
def init_db():
    conn = sqlite3.connect(DB_PATH)
    # Create table if missing
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
    )
    if not cur.fetchone():
        conn.execute(
            "CREATE TABLE tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "chat_id INTEGER NOT NULL,"
            "description TEXT NOT NULL,"
            "remind_at DATETIME,"
            "is_done BOOLEAN NOT NULL DEFAULT 0,"
            "question_interval INTEGER NOT NULL DEFAULT 0,"
            "question_enabled BOOLEAN NOT NULL DEFAULT 0,"
            "next_question_at DATETIME,"
            "next_reminder_at DATETIME"
            ")"
        )
    else:
        # Migrate schema: add new columns if absent
        info = conn.execute("PRAGMA table_info(tasks)").fetchall()
        cols = [row[1] for row in info]
        if 'next_question_at' not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN next_question_at DATETIME")
        if 'next_reminder_at' not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN next_reminder_at DATETIME")
    conn.commit()
    conn.close()


def add_task(chat_id, desc, remind_dt):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO tasks (chat_id,description,remind_at) VALUES (?,?,?)",
        (chat_id, desc, remind_dt.isoformat())
    )
    task_id = cur.lastrowid
    # initialize next_reminder_at to the due time
    conn.execute(
        "UPDATE tasks SET next_reminder_at=? WHERE id=?",
        (remind_dt.isoformat(), task_id)
    )
    conn.commit()
    conn.close()
    return task_id


def list_tasks(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT id,description,remind_at,question_interval,question_enabled "
        "FROM tasks WHERE chat_id=? AND is_done=0",
        (chat_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_done(task_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE tasks SET is_done=1 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()


def set_question_prefs(task_id, interval_min, enabled):
    conn = sqlite3.connect(DB_PATH)
    if enabled and interval_min > 0:
        next_q = datetime.now() + timedelta(minutes=interval_min)
        conn.execute(
            "UPDATE tasks SET question_interval=?,question_enabled=?,next_question_at=? WHERE id=?",
            (interval_min, enabled, next_q.isoformat(), task_id)
        )
    else:
        conn.execute(
            "UPDATE tasks SET question_interval=?,question_enabled=?,next_question_at=NULL WHERE id=?",
            (interval_min, enabled, task_id)
        )
    conn.commit()
    conn.close()

# — Command Handlers —
async def start(update: Update, ctx: CallbackContext):
    await update.message.reply_text(
        "👋 *TaskBot* is online!\n\n"
        "/add — create a task  \n"
        "/list — view current tasks  \n"
        "/done — mark task done\n\n"
        "Example:\n"
        "`/add Write report at 2025-07-10 18:00`",
        parse_mode="Markdown"
    )

async def add(update: Update, ctx: CallbackContext):
    text = update.message.text.partition(" ")[2]
    if " at " not in text:
        return await update.message.reply_text(
            "❌ Usage: `/add TASK_DESCRIPTION at YYYY-MM-DD HH:MM`",
            parse_mode="Markdown"
        )
    desc, _, timestr = text.rpartition(" at ")
    dt = dateparser.parse(timestr)
    if not dt:
        return await update.message.reply_text("❌ Could not parse date/time.")

    task_id = add_task(update.effective_chat.id, desc, dt)

    keyboard = [
        [InlineKeyboardButton("5 min", callback_data=f"qi|{task_id}|5"),
         InlineKeyboardButton("15 min", callback_data=f"qi|{task_id}|15")],
        [InlineKeyboardButton("1 hr", callback_data=f"qi|{task_id}|60"),
         InlineKeyboardButton("Off", callback_data=f"qi|{task_id}|0")]
    ]
    await update.message.reply_text(
        f"✅ Task *#{task_id}* scheduled: _{desc}_ at {dt}\n"
        "Choose question‑style reminder interval:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def listall(update: Update, ctx: CallbackContext):
    tasks = list_tasks(update.effective_chat.id)
    if not tasks:
        return await update.message.reply_text("You have no active tasks.")

    lines = []
    for tid, desc, remind_at, qi, qon in tasks:
        qtext = f"{qi} min" if qon else "off"
        lines.append(
            f"⌛ ID {tid} – {desc}\n"
            f"    • due: {remind_at}  |  question: {qtext}"
        )
    await update.message.reply_text("\n\n".join(lines))

async def done_cmd(update: Update, ctx: CallbackContext):
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text(
            "❌ Usage: `/done TASK_ID`", parse_mode="Markdown"
        )
    tid = int(args[0])
    mark_done(tid)
    await update.message.reply_text(f"🗹 Task `{tid}` marked done.", parse_mode="Markdown")

async def question_interval_cb(update: Update, ctx: CallbackContext):
    query = update.callback_query
    await query.answer()
    _, tid, mins = query.data.split("|")
    tid, mins = int(tid), int(mins)
    enabled = 1 if mins > 0 else 0
    set_question_prefs(tid, mins, enabled)
    text = "disabled ❌" if enabled == 0 else f"every *{mins} minutes*"
    await query.edit_message_text(
        f"Question reminders for task `{tid}` {text}.",
        parse_mode="Markdown"
    )

# — Reminder Scheduler —

def safe_parse(dt_str):
    if isinstance(dt_str, str):
        try:
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            logger.error(f"Bad datetime format: {dt_str}")
            return None
    return None


def check_reminders(app):
    now = datetime.now()
    logger.info(f"🔎 check_reminders @ {now.isoformat()}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT id,chat_id,description,question_interval,question_enabled,"  
        "next_question_at,next_reminder_at "
        "FROM tasks WHERE is_done=0"
    )
    rows = cur.fetchall()
    logger.info(f"   → {len(rows)} tasks loaded")

    for tid, chat_id, desc, qi, qon, nq_str, nr_str in rows:
        # Safely parse datetimes
        next_q = safe_parse(nq_str)
        next_r = safe_parse(nr_str) or now

        # question reminders until due time
        if qon and next_q and next_q <= now and next_r > now:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id,
                    f"❓ Are you still working on *{desc}*? (next in {qi} min)",
                    parse_mode="Markdown"
                ), LOOP
            )
            conn.execute(
                "UPDATE tasks SET next_question_at=? WHERE id=?",
                ((next_q + timedelta(minutes=qi)).isoformat(), tid)
            )

        # due reminders at and after due time
        if next_r <= now:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id,
                    f"⏰ Reminder: *{desc}* (ID {tid})",
                    parse_mode="Markdown"
                ), LOOP
            )
            bump = timedelta(minutes=qi) if qi > 0 else timedelta(minutes=1)
            conn.execute(
                "UPDATE tasks SET next_reminder_at=? WHERE id=?",
                ((next_r + bump).isoformat(), tid)
            )

    conn.commit()
    conn.close()
    conn.close()

# — Main Entrypoint —
def main():
    init_db()
    app = ApplicationBuilder().token(config.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", listall))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(question_interval_cb, pattern=r"^qi\|"))

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_reminders, "interval", minutes=1, args=(app,))
    scheduler.start()

    app.run_polling()


if __name__ == "__main__":
    if os.getenv("DEV"):
        from watchgod import run_process
        run_process(BASE_DIR, target=main)
    else:
        main()
