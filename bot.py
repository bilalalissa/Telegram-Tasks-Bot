import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta

import dateparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from telegram.constants import ParseMode
import re
from dotenv import load_dotenv
from telegram.ext import ConversationHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import json
from datetime import datetime as dt

import config  # loads TELEGRAM_TOKEN


# — Database file (absolute path) —
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tasks.db")

print("DEBUG: THIS IS THE TOP OF bot.py")
with open("debug_log.txt", "a") as f:
    f.write("bot.py started\n")

# --- Debug JSON Logger ---
DEBUG_LOG_JSON = os.path.join(BASE_DIR, "debug_log.json")

def log_debug_event(event_type, title, msg, userid=None, chatid=None, extra=None):
    # Load current log number
    try:
        with open(DEBUG_LOG_JSON, "r") as f:
            logs = json.load(f)
    except Exception:
        logs = []
    number = logs[-1]["number"] + 1 if logs else 1
    now = dt.now()
    entry = {
        "number": number,
        "type": event_type,
        "title": title,
        "msg": msg,
        "userid": userid,
        "chatid": chatid,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
    }
    if extra:
        entry.update(extra)
    logs.append(entry)
    with open(DEBUG_LOG_JSON, "w") as f:
        json.dump(logs, f, indent=2)

# Log bot start
log_debug_event(
    event_type="system",
    title="Bot started",
    msg="bot.py started",
)

# Conversation states
TASK_DESC, TASK_DATE, TASK_TIME, TASK_TOPIC, TASK_SUBJECT, TASK_INTERVAL, TASK_CONFIRM = range(7)

# — Logging setup —
logging.basicConfig(
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# — Asyncio loop reference —
# LOOP = asyncio.get_event_loop()


# Load admin credentials and blocked users from .env
ADMIN_USERS = os.getenv('ADMIN_USERS', '').split(',') if os.getenv('ADMIN_USERS') else []
ADMIN_PASSWORDS = os.getenv('ADMIN_PASSWORDS', '').split(',') if os.getenv('ADMIN_PASSWORDS') else []
admin_credentials = dict(zip(ADMIN_USERS, ADMIN_PASSWORDS))
admineba = [int(uid) for uid in os.getenv('ADMIN_IDS', '').split(',') if uid.strip().isdigit()] if os.getenv('ADMIN_IDS') else []
blocked_admins = set(int(uid) for uid in os.getenv('BLOCKED_USERS', '').split(',') if uid.strip().isdigit()) if os.getenv('BLOCKED_USERS') else set()

# In-memory map: user_id -> last task_id for interval selection
interval_task_map = {}

# Helper to parse interval string to minutes
INTERVAL_LABELS = [
    ("min", 1),
    ("hr", 60),
    ("day", 1440),
    ("wk", 10080),
    ("mo", 43200),
    ("yr", 525600),
]
def parse_interval_label(label):
    label = label.strip().lower()
    if label == "off":
        return 0
    for unit, mult in INTERVAL_LABELS:
        if unit in label:
            num = int(label.split()[0])
            return num * mult
    return None


# Add a helper to check if a user is blocked
def is_user_blocked(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,))
    blocked = cur.fetchone() is not None
    conn.close()
    return blocked

from functools import wraps

def block_check(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if is_user_blocked(user_id):
            await update.message.reply_text("❌ You are blocked from using this bot.")
            return
        return await func(update, ctx, *args, **kwargs)
    return wrapper

# Handler for interval reply
@block_check
async def interval_reply_handler(update: Update, ctx: CallbackContext):
    user_id = update.effective_user.id
    text = update.message.text.strip().lower()
    mins = parse_interval_label(text)
    if mins is None:
        return  # Not an interval reply, ignore
    # Find last task for this user
    task_id = interval_task_map.get(user_id)
    if not task_id:
        await update.message.reply_text("No task found to set interval for. Please add or edit a task first.")
        return
    enabled = 1 if mins > 0 else 0
    set_question_prefs(task_id, mins, enabled)
    del interval_task_map[user_id]
    if enabled:
        await update.message.reply_text(f"✅ Reminding interval set: every {text}.")
    else:
        await update.message.reply_text("❌ Reminders/questions turned off for this task.")


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
            "user_id INTEGER,"
            "user_task_id INTEGER,"
            "description TEXT NOT NULL,"
            "remind_at DATETIME,"
            "is_done BOOLEAN NOT NULL DEFAULT 0,"
            "question_interval INTEGER NOT NULL DEFAULT 0,"
            "question_enabled BOOLEAN NOT NULL DEFAULT 0,"
            "next_question_at DATETIME,"
            "next_reminder_at DATETIME"
            ")"
        )
    # Always ensure admin_sessions and blocked_users tables exist
    conn.execute(
        "CREATE TABLE IF NOT EXISTS admin_sessions ("
        "user_id INTEGER PRIMARY KEY,"
        "username TEXT,"
        "login_time DATETIME"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS blocked_users ("
        "user_id INTEGER PRIMARY KEY,"
        "blocked_at DATETIME"
        ")"
    )
    # Migrate schema: add new columns if absent
    info = conn.execute("PRAGMA table_info(tasks)").fetchall()
    cols = [row[1] for row in info]
    if 'user_id' not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER")
    if 'user_task_id' not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN user_task_id INTEGER")
    if 'next_question_at' not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN next_question_at DATETIME")
    if 'next_reminder_at' not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN next_reminder_at DATETIME")
    if 'topic' not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN topic TEXT")
    if 'subject' not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN subject TEXT")
    # Assign user_task_id for existing tasks if missing
    cur = conn.execute("SELECT chat_id, user_id FROM tasks WHERE user_task_id IS NULL GROUP BY chat_id, user_id")
    for chat_id, user_id in cur.fetchall():
        tcur = conn.execute(
            "SELECT id FROM tasks WHERE chat_id=? AND user_id=? ORDER BY id",
            (chat_id, user_id)
        )
        for idx, (tid,) in enumerate(tcur.fetchall(), 1):
            conn.execute("UPDATE tasks SET user_task_id=? WHERE id=?", (idx, tid))
    conn.commit()
    conn.close()


def add_task(chat_id, user_id, desc, remind_dt, topic=None, subject=None):
    conn = sqlite3.connect(DB_PATH)
    # Get next user_task_id for this user in this chat
    cur = conn.execute(
        "SELECT COALESCE(MAX(user_task_id), 0) + 1 FROM tasks WHERE chat_id=? AND user_id=?",
        (chat_id, user_id)
    )
    user_task_id = cur.fetchone()[0]
    cur = conn.execute(
        "INSERT INTO tasks (chat_id, user_id, user_task_id, description, remind_at, topic, subject) VALUES (?,?,?,?,?,?,?)",
        (chat_id, user_id, user_task_id, desc, remind_dt.isoformat(), topic, subject)
    )
    task_id = cur.lastrowid
    # initialize next_reminder_at to the due time
    conn.execute(
        "UPDATE tasks SET next_reminder_at=? WHERE id=?",
        (remind_dt.isoformat(), task_id)
    )
    conn.commit()
    conn.close()
    return task_id, user_task_id

async def list_tasks(update: Update, ctx: CallbackContext):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT user_task_id, description, remind_at, is_done, topic, subject FROM tasks WHERE chat_id=? AND user_id=? ORDER BY is_done, remind_at",
        (chat_id, user_id)
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return await update.message.reply_text("You have no tasks.")
    lines = []
    for utid, desc, remind_at, is_done, topic, subject in rows:
        status = "✅" if is_done else "🕒"
        due = f" (due {remind_at})" if remind_at else ""
        extra = ""
        if topic:
            extra += f"[Topic: {topic}] "
        if subject:
            extra += f"[Subject: {subject}] "
        lines.append(f"[{utid}] {status} {extra}{desc}{due}")
    await update.message.reply_text("\n".join(lines))


def mark_done(task_id, user_id=None, admin=False):
    conn = sqlite3.connect(DB_PATH)
    if admin:
        conn.execute("UPDATE tasks SET is_done=1 WHERE id=?", (task_id,))
    else:
        conn.execute("UPDATE tasks SET is_done=1 WHERE id=? AND user_id=?", (task_id, user_id))
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

# Apply @block_check to all user and admin command handlers
@block_check
async def start(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/start",
        msg="User started the bot",
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    # Use plain text for welcome message to avoid Telegram parse errors
    welcome = (
        "👋 Welcome to TaskBot \n\n"
        "TaskBot is a Telegram bot designed to help you manage tasks with:\n"
        "- Scheduled due-time reminders\n"
        "- Optional follow-up questions before the due time\n"
        "- Recurring alarms until tasks are completed\n\n"
        "How to use?\n"
        "/add   — Add a new task\n"
        "/list  — List your tasks\n"
        "/done  — Mark a task as done (/done <TASK_ID>)\n"
        "/edit  — Edit your task (/edit <TASK_ID>)\n"
        "/del   — Delete your task (/del <TASK_ID>)\n"
        "/info  — Show task info (/info TASK_ID)\n"
        "/menu  — Show this menu\n"
        "\n\nAt any time:\n ⏩ /skip \n 🔚 /cancel"
        
    )
    await update.message.reply_text(
        welcome,
        reply_markup=ReplyKeyboardRemove()
    )

@block_check
async def add(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/add",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    text = update.message.text.partition(" ")[2]
    # Parse topic and subject if present (robust)
    import re
    topic = None
    subject = None
    # Extract topic and subject, remove them from text
    topic_match = re.search(r"topic=([^\s]+)", text)
    subject_match = re.search(r"subject=([^\s]+)", text)
    if topic_match:
        topic = topic_match.group(1).strip()
        text = re.sub(r"topic=[^\s]+", "", text)
    if subject_match:
        subject = subject_match.group(1).strip()
        text = re.sub(r"subject=[^\s]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()  # collapse multiple spaces
    # Now split on the last ' at '
    if " at " not in text:
        return await update.message.reply_text(
            "❌ Usage: \n/add then hit Enter/return key, and follow up with the steps.\nOr\n/add <SUBJECT> <TOPIC> <DESCRIPTION> at YYYY-MM-DD HH:MM\nOr\n/skip to skip or /cancel to cancel task.",
            parse_mode=None
        )
    desc, _, timestr = text.rpartition(" at ")
    desc = desc.strip()
    timestr = timestr.strip()
    if not desc and not timestr:
        return await update.message.reply_text(
            "❌ Usage: \n/add then hit Enter/return key, and follow up with the steps.\nOr\n/add <SUBJECT> <TOPIC> <DESCRIPTION> at YYYY-MM-DD HH:MM\nOr\n/skip to skip or /cancel to cancel task.",
            parse_mode=None
        )
    if not desc:
        return await update.message.reply_text(
            "❌ Usage: \n/add then hit Enter/return key, and follow up with the steps.\nOr\n/add <SUBJECT> <TOPIC> <DESCRIPTION> at YYYY-MM-DD HH:MM\nOr\n/skip to skip or /cancel to cancel task.",
            parse_mode=None
        )
    if not timestr:
        return await update.message.reply_text(
            "❌ Usage: \n/add then hit Enter/return key, and follow up with the steps.\nOr\n/add <SUBJECT> <TOPIC> <DESCRIPTION> at YYYY-MM-DD HH:MM\nOr\n/skip to skip or /cancel to cancel task.",
            parse_mode=None
        )
    dt = dateparser.parse(timestr)
    if not dt:
        return await update.message.reply_text(
            "❌ Could not parse date/time. Please use a format like YYYY-MM-DD HH:MM.",
            parse_mode=None
        )

    user_id = update.effective_user.id
    task_id, user_task_id = add_task(update.effective_chat.id, user_id, desc, dt, topic, subject)

    # Dynamic intervals
    intervals = get_dynamic_intervals(dt)
    keyboard = [[str(i) + ' min' if i < 60 else (str(i//60) + ' hr' if i < 1440 else (str(i//1440) + ' day' if i < 10080 else (str(i//10080) + ' wk' if i < 43200 else (str(i//43200) + ' mo' if i < 525600 else str(i//525600) + ' yr')))) for i in intervals], ['off']]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    details = f"_" + desc + "_"
    if topic:
        details = f"[Topic: {topic}] " + details
    if subject:
        details = f"[Subject: {subject}] " + details
    await update.message.reply_text(
        f"✅ Task *#{user_task_id}* scheduled: {details} at {dt}\nChoose reminder/question interval:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    interval_task_map[user_id] = task_id

@block_check
async def listall(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/list",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    is_admin = is_admin_user(update) or user_id in admineba
    conn = sqlite3.connect(DB_PATH)
    if is_admin:
        cur = conn.execute(
            "SELECT id, user_task_id, description, remind_at, is_done, user_id, topic, subject FROM tasks WHERE chat_id=? ORDER BY user_id, is_done, remind_at",
            (chat_id,)
        )
        rows = cur.fetchall()
        if not rows:
            conn.close()
            return await update.message.reply_text("No tasks found in this chat.")
        lines = []
        for tid, utid, desc, remind_at, is_done, uid, topic, subject in rows:
            status = "✅" if is_done else "🕒"
            due = f" (due {remind_at})" if remind_at else ""
            extra = ""
            if topic:
                extra += f"[Topic: {topic}] "
            if subject:
                extra += f"[Subject: {subject}] "
            lines.append(f"[{utid}] {status} {extra}{desc}{due} (user {uid})")
        conn.close()
        await update.message.reply_text("\n".join(lines))
    else:
        cur = conn.execute(
            "SELECT user_task_id, description, remind_at, is_done, topic, subject FROM tasks WHERE chat_id=? AND user_id=? ORDER BY is_done, remind_at",
            (chat_id, user_id)
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return await update.message.reply_text("You have no tasks.")
        lines = []
        for utid, desc, remind_at, is_done, topic, subject in rows:
            status = "✅" if is_done else "🕒"
            due = f" (due {remind_at})" if remind_at else ""
            extra = ""
            if topic:
                extra += f"[Topic: {topic}] "
            if subject:
                extra += f"[Subject: {subject}] "
            lines.append(f"[{utid}] {status} {extra}{desc}{due}")
        await update.message.reply_text("\n".join(lines))

@block_check
async def done_cmd(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/done",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text(
            "❌ Usage: /done <TASK_ID>\n(TASK_ID is as shown in /list)", parse_mode=None
        )
    utid = int(args[0])
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    # Look up real id
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM tasks WHERE chat_id=? AND user_id=? AND user_task_id=?", (chat_id, user_id, utid))
    row = cur.fetchone()
    if not row:
        conn.close()
        return await update.message.reply_text("Task not found.")
    tid = row[0]
    conn.close()
    mark_done(tid, user_id, admin=False)
    await update.message.reply_text(f"🗹 Task `{utid}` marked done.", parse_mode="Markdown")

@block_check
async def question_interval_cb(update: Update, ctx: CallbackContext):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    if len(parts) != 3 or not query.data.startswith("qi|"):
        # Not the expected format, ignore or log
        return
    _, tid, mins = parts
    tid, mins = int(tid), int(mins)
    enabled = 1 if mins > 0 else 0
    set_question_prefs(tid, mins, enabled)
    text = "disabled ❌" if enabled == 0 else f"every *{mins} minutes*"
    await query.edit_message_text(
        f"Question reminders for task `{tid}` {text}.",
        parse_mode="Markdown"
    )

# Admin Login
@block_check
async def alogin(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/alogin",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    args = ctx.args
    if len(args) != 2:
        return await update.message.reply_text(
            "Usage: /alogin <username> <password>",
            parse_mode=ParseMode.MARKDOWN
        )
    username, password = args
    if username in admin_credentials and admin_credentials[username] == password:
        admin_login(user_id, username)
        await update.message.reply_text(
            f"✅ Admin login successful as *{username}*.",
            parse_mode=ParseMode.MARKDOWN
        )
        await update.message.reply_text(
            "👋 *Welcome, admin!* \n\nUse /m to see the admin menu with all available commands.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        return await update.message.reply_text(
            "❌ Invalid admin credentials.",
            parse_mode=ParseMode.MARKDOWN
        )

# Admin Logout
@block_check
async def alogout(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/alogout",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    if is_admin_logged_in(user_id):
        admin_logout(user_id)
        await update.message.reply_text("✅ Admin logged out.")
        await start(update, ctx)
    else:
        await update.message.reply_text("You are not logged in as admin.")

# User Edit
@block_check
async def edit(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/edit",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text(
            "Usage: /edit <TASK_ID> \n(TASK_ID is as shown in /list)",
            parse_mode=None
        )
    utid = int(args[0])
    rest = " ".join(args[1:])
    # If no fields provided, launch the edit wizard
    if not rest.strip():
        # Look up the real task id for this user and chat
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT id FROM tasks WHERE chat_id=? AND user_id=? AND user_task_id=?", (chat_id, user_id, utid))
        row = cur.fetchone()
        conn.close()
        if not row:
            return await update.message.reply_text("Task not found or you do not have permission to edit it.")
        tid = row[0]
        ctx.user_data.clear()
        ctx.user_data['edit_task_id'] = tid
        await update.message.reply_text("✏️ Let's edit this task. Please enter the new description (or type /skip to keep current):")
        return EDIT_DESC
    # Otherwise, do the quick edit as before
    desc = None
    due = None
    topic = None
    subject = None
    desc_match = re.search(r"desc=([^\]]+?)(?= due=| topic=| subject=|$)", rest)
    due_match = re.search(r"due=([^\]]+?)(?= desc=| topic=| subject=|$)", rest)
    topic_match = re.search(r"topic=([^\]]+?)(?= desc=| due=| subject=|$)", rest)
    subject_match = re.search(r"subject=([^\]]+?)(?= desc=| due=| topic=|$)", rest)
    if desc_match:
        desc = desc_match.group(1).strip()
    if due_match:
        due = due_match.group(1).strip()
    if topic_match:
        topic = topic_match.group(1).strip()
    if subject_match:
        subject = subject_match.group(1).strip()
    if desc is None and due is None and topic is None and subject is None:
        return await update.message.reply_text(
            "Nothing to edit. Provide desc=, due=, topic=, or subject=.",
            parse_mode=None
        )
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id, remind_at FROM tasks WHERE chat_id=? AND user_id=? AND user_task_id=?", (chat_id, user_id, utid))
    row = cur.fetchone()
    if not row:
        conn.close()
        return await update.message.reply_text("Task not found or you do not have permission to edit it.")
    tid, remind_at = row
    updates = []
    params = []
    if desc is not None:
        updates.append("description=?")
        params.append(desc)
    if due is not None:
        from dateparser import parse as parse_date
        dt = parse_date(due)
        if not dt:
            conn.close()
            return await update.message.reply_text("Invalid due date format.")
        updates.append("remind_at=?")
        params.append(dt.isoformat())
    if topic is not None:
        updates.append("topic=?")
        params.append(topic)
    if subject is not None:
        updates.append("subject=?")
        params.append(subject)
    params.append(tid)
    conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Task {utid} updated.")
    # Always prompt for interval after quick edit
    # Use the latest due date (remind_at) from DB
    with sqlite3.connect(DB_PATH) as conn2:
        cur2 = conn2.execute("SELECT remind_at FROM tasks WHERE id=?", (tid,))
        row2 = cur2.fetchone()
        remind_at2 = row2[0] if row2 else None
    if remind_at2:
        dt2 = dateparser.parse(remind_at2)
        if dt2:
            intervals = get_dynamic_intervals(dt2)
            keyboard = ReplyKeyboardMarkup([[str(i) + ' min' if i < 60 else (str(i//60) + ' hr' if i < 1440 else (str(i//1440) + ' day' if i < 10080 else (str(i//10080) + ' wk' if i < 43200 else (str(i//43200) + ' mo' if i < 525600 else str(i//525600) + ' yr')))) for i in intervals], ['off']], one_time_keyboard=True, resize_keyboard=True)
            await update.message.reply_text(
                f"Choose new reminder/question interval for task {utid}:",
                reply_markup=keyboard
            )
    interval_task_map[user_id] = tid

# User Delete
@block_check
async def delete(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/del",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text(
            "Usage: /del <TASK_ID>\n(TASK_ID is as shown in /list)",
            parse_mode=None
        )
    utid = int(args[0])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM tasks WHERE chat_id=? AND user_id=? AND user_task_id=?", (chat_id, user_id, utid))
    row = cur.fetchone()
    if not row:
        conn.close()
        return await update.message.reply_text("Task not found or you do not have permission to delete it.")
    tid = row[0]
    conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Task {utid} deleted.")

# Update user menu to include /edit and /del
@block_check
async def slash_menu(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/menu",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    is_admin = is_admin_user(update) or user_id in admineba
    menu = (
        "TaskBot Commands Menu:\n\n"
        "/add       — Add a new task\n"
        "/list      — List your tasks\n"
        "/done      — Mark a task as done (/done <TASK_ID>)\n"
        "/edit      — Edit your task (/edit <TASK_ID>)\n"
        "/del       — Delete your task (/del <TASK_ID>)\n"
        "/info      — Show task info (/info <TASK_ID>)\n"
        "/menu      — Show this menu\n"
        "\n\nAt any time:\n ⏩ /skip \n 🔚 /cancel"
    )
    await update.message.reply_text(menu)

# Admin Menu
@block_check
async def admin_menu(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/admin_menu",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    if not is_admin_logged_in(user_id):
        return  # Do not respond if not admin
    await update.message.reply_text(
        "*Admin Menu*\n"
        "/aadd USER_ID [topic=TOPIC] [subject=SUBJECT] DESCRIPTION at YYYY-MM-DD HH:MM — Add a task for a user\n"
        "/alist — List all users and tasks in this chat\n"
        "/alist all — List all users and tasks in all chats (global)\n"
        "/aedit TASK_ID [desc=NEW_DESCRIPTION] [due=YYYY-MM-DD HH:MM] [topic=NEW_TOPIC] [subject=NEW_SUBJECT] — Edit any task by global ID\n"
        "/adone TASK_ID — Mark any task as done by global ID\n"
        "/adel TASK_ID — Delete any task by global ID\n"
        "/aulist — List users in this chat\n"
        "/aulist all — List users in all chats (global)\n"
        "/audel USER_ID [CHAT_ID|all] — Delete all tasks for a user (optionally in a specific chat or all chats)\n"
        "/achats — List all chat IDs with tasks\n"
        "/ausers — List all user IDs with tasks\n"
        "/ablock USER_ID — Block a user from using the bot\n"
        "/aunblock USER_ID — Unblock a user\n"
        "/alogin — Log in as admin\n"
        "/alogout — Log out as admin\n",
        parse_mode=ParseMode.MARKDOWN
    )

# Admin List (global)
@block_check
async def alist(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/alist",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    conn = sqlite3.connect(DB_PATH)
    if args and args[0] == "all":
        cur = conn.execute(
            "SELECT chat_id, user_id, id, description, remind_at, is_done, topic, subject FROM tasks ORDER BY chat_id, user_id, is_done, remind_at"
        )
        rows = cur.fetchall()
        if not rows:
            conn.close()
            return await update.message.reply_text("No tasks found in any chat.")
        lines = []
        for chat_id, uid, tid, desc, remind_at, is_done, topic, subject in rows:
            status = "✅ done" if is_done else "⏳ active"
            extra = ""
            if topic:
                extra += f"[Topic: {topic}] "
            if subject:
                extra += f"[Subject: {subject}] "
            lines.append(f"Chat {chat_id} | User {uid}: Task {tid} — {extra}{desc}\n    • due: {remind_at} | {status}")
        conn.close()
        await update.message.reply_text("\n\n".join(lines))
    else:
        chat_id = update.effective_chat.id
        cur = conn.execute(
            "SELECT user_id, id, description, remind_at, is_done, topic, subject FROM tasks WHERE chat_id=? ORDER BY user_id, is_done, remind_at",
            (chat_id,)
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return await update.message.reply_text("No tasks found in this chat.")
        lines = []
        for uid, tid, desc, remind_at, is_done, topic, subject in rows:
            status = "✅ done" if is_done else "⏳ active"
            extra = ""
            if topic:
                extra += f"[Topic: {topic}] "
            if subject:
                extra += f"[Subject: {subject}] "
            lines.append(f"User {uid}: Task {tid} — {extra}{desc}\n    • due: {remind_at} | {status}")
        await update.message.reply_text("\n\n".join(lines))

# Admin Edit (global)
@block_check
async def aedit(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/aedit",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text(
            "Usage: /aedit TASK_ID [desc=NEW_DESCRIPTION] [due=YYYY-MM-DD HH:MM] [topic=NEW_TOPIC] [subject=NEW_SUBJECT]",
            parse_mode=ParseMode.MARKDOWN
        )
    tid = int(args[0])
    rest = " ".join(args[1:])
    desc = None
    due = None
    topic = None
    subject = None
    desc_match = re.search(r"desc=([^\]]+?)(?= due=| topic=| subject=|$)", rest)
    due_match = re.search(r"due=([^\]]+?)(?= desc=| topic=| subject=|$)", rest)
    topic_match = re.search(r"topic=([^\]]+?)(?= desc=| due=| subject=|$)", rest)
    subject_match = re.search(r"subject=([^\]]+?)(?= desc=| due=| topic=|$)", rest)
    if desc_match:
        desc = desc_match.group(1).strip()
    if due_match:
        due = due_match.group(1).strip()
    if topic_match:
        topic = topic_match.group(1).strip()
    if subject_match:
        subject = subject_match.group(1).strip()
    if desc is None and due is None and topic is None and subject is None:
        return await update.message.reply_text(
            "Nothing to edit. Provide desc=, due=, topic=, or subject=.",
            parse_mode=ParseMode.MARKDOWN
        )
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM tasks WHERE id=?", (tid,))
    if not cur.fetchone():
        conn.close()
        return await update.message.reply_text("Task not found.")
    updates = []
    params = []
    if desc is not None:
        updates.append("description=?")
        params.append(desc)
    if due is not None:
        from dateparser import parse as parse_date
        dt = parse_date(due)
        if not dt:
            conn.close()
            return await update.message.reply_text("Invalid due date format.")
        updates.append("remind_at=?")
        params.append(dt.isoformat())
    if topic is not None:
        updates.append("topic=?")
        params.append(topic)
    if subject is not None:
        updates.append("subject=?")
        params.append(subject)
    params.append(tid)
    conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Task {tid} updated.")

# Admin Done (global)
@block_check
async def adone(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/adone",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text("Usage: /adone TASK_ID")
    tid = int(args[0])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM tasks WHERE id=?", (tid,))
    if not cur.fetchone():
        conn.close()
        return await update.message.reply_text("Task not found.")
    conn.execute("UPDATE tasks SET is_done=1 WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Task {tid} marked as done.")

# Admin Delete Task (global)
@block_check
async def adel(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/adel",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text("Usage: /adel TASK_ID")
    tid = int(args[0])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM tasks WHERE id=?", (tid,))
    if not cur.fetchone():
        conn.close()
        return await update.message.reply_text("Task not found.")
    conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Task {tid} deleted.")

# Admin User List (global)
@block_check
async def aulist(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/aulist",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    conn = sqlite3.connect(DB_PATH)
    if args and args[0] == "all":
        cur = conn.execute("SELECT DISTINCT user_id, chat_id FROM tasks WHERE user_id IS NOT NULL")
        users = [f"User {row[0]} in Chat {row[1]}" for row in cur.fetchall()]
        conn.close()
        if not users:
            return await update.message.reply_text("No users found in any chat.")
        await update.message.reply_text("Users across all chats:\n" + "\n".join(users))
    else:
        chat_id = update.effective_chat.id
        cur = conn.execute("SELECT DISTINCT user_id FROM tasks WHERE chat_id=?", (chat_id,))
        users = [str(row[0]) for row in cur.fetchall() if row[0] is not None]
        conn.close()
        if not users:
            return await update.message.reply_text("No users found in this chat.")
        await update.message.reply_text("Users in this chat:\n" + "\n".join(users))

# Admin User Delete (global)
@block_check
async def audel(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/audel",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text("Usage: /audel USER_ID [CHAT_ID|all]")
    uid = int(args[0])
    conn = sqlite3.connect(DB_PATH)
    if len(args) > 1 and args[1] == "all":
        cur = conn.execute("SELECT id FROM tasks WHERE user_id=?", (uid,))
        if not cur.fetchone():
            conn.close()
            return await update.message.reply_text("No tasks found for this user in any chat.")
        conn.execute("DELETE FROM tasks WHERE user_id=?", (uid,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"All tasks for user {uid} deleted in all chats.")
    else:
        chat_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else update.effective_chat.id
        cur = conn.execute("SELECT id FROM tasks WHERE chat_id=? AND user_id=?", (chat_id, uid))
        if not cur.fetchone():
            conn.close()
            return await update.message.reply_text("No tasks found for this user in this chat.")
        conn.execute("DELETE FROM tasks WHERE chat_id=? AND user_id=?", (chat_id, uid))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"All tasks for user {uid} deleted in chat {chat_id}.")

# Admin Block
@block_check
async def ablock(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/ablock",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text("Usage: /ablock USER_ID")
    uid = int(args[0])
    block_user(uid)
    await update.message.reply_text(f"User {uid} is now blocked from using the bot.")

# Admin Unblock
@block_check
async def aunblock(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/aunblock",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text("Usage: /aunblock USER_ID")
    uid = int(args[0])
    if is_user_blocked(uid):
        unblock_user(uid)
        await update.message.reply_text(f"User {uid} is now unblocked.")
    else:
        await update.message.reply_text(f"User {uid} was not blocked.")

# Admin List Chats
@block_check
async def achats(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/achats",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT DISTINCT chat_id FROM tasks")
    chats = [str(row[0]) for row in cur.fetchall()]
    conn.close()
    if not chats:
        return await update.message.reply_text("No chats found.")
    await update.message.reply_text("Chats with tasks:\n" + "\n".join(chats))

# Admin List All Users (across all chats)
@block_check
async def ausers(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/ausers",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT DISTINCT user_id FROM tasks WHERE user_id IS NOT NULL")
    users = [str(row[0]) for row in cur.fetchall()]
    conn.close()
    if not users:
        return await update.message.reply_text("No users found.")
    await update.message.reply_text("All users with tasks:\n" + "\n".join(users))

# — Reminder Scheduler —

def safe_parse(dt_str):
    if isinstance(dt_str, str):
        try:
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            logger.error(f"Bad datetime format: {dt_str}")
            return None
    return None


# --- Inline Action Buttons for Reminders/Questions ---
def build_task_action_keyboard(task_id, enable_reenable=False):
    buttons = [
        [InlineKeyboardButton("Snooze", callback_data=f"taskact|snooze|{task_id}"),
         InlineKeyboardButton("Dismiss", callback_data=f"taskact|dismiss|{task_id}")],
        [InlineKeyboardButton("Edit", callback_data=f"taskact|edit|{task_id}"),
         InlineKeyboardButton("Done", callback_data=f"taskact|done|{task_id}")]
    ]
    if enable_reenable:
        buttons.insert(1, [InlineKeyboardButton("Reenable", callback_data=f"taskact|reenable|{task_id}")])
    return InlineKeyboardMarkup(buttons)

# --- Reminder Scheduler ---
def check_reminders(app, loop):
    now = datetime.now()
    logger.info(f"🔎 check_reminders @ {now.isoformat()}")

    # Use context manager and fetch all rows first
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT id,chat_id,description,question_interval,question_enabled,"
            "next_question_at,next_reminder_at,user_task_id,remind_at FROM tasks WHERE is_done=0"
        )
        rows = cur.fetchall()
    logger.info(f"   → {len(rows)} tasks loaded")

    for tid, chat_id, desc, qi, qon, nq_str, nr_str, user_task_id, remind_at in rows:
        # Safely parse datetimes
        next_q = safe_parse(nq_str)
        next_r = safe_parse(nr_str) or now
        logger.info(f"Task {tid}: now={now}, next_q={next_q}, next_r={next_r}, qon={qon}, qi={qi}")

        # question reminders until due time
        if qon and next_q and next_q <= now and next_r > now:
            logger.info(f"Task {tid}: Sending QUESTION (next_q <= now and next_r > now)")
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id,
                    f"❓ Are you still working on *{desc}*? (task #{user_task_id})",
                    parse_mode="Markdown",
                    reply_markup=build_task_action_keyboard(tid, enable_reenable=False)
                ),
                loop
            )
            # Reopen connection only for update
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE tasks SET next_question_at=? WHERE id=?",
                    ((next_q + timedelta(minutes=qi)).isoformat(), tid)
                )
            # Send reminder info after question
            send_reminder_info(chat_id, user_task_id, remind_at, qi, app.bot, loop)

        # due reminders at and after due time
        if next_r <= now:
            logger.info(f"Task {tid}: Sending REMINDER (next_r <= now)")
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id,
                    f"⏰ Reminder: *{desc}* (task #{user_task_id})",
                    parse_mode="Markdown",
                    reply_markup=build_task_action_keyboard(tid, enable_reenable=True)
                ),
                loop
            )
            bump = timedelta(minutes=qi) if qi > 0 else timedelta(minutes=1)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE tasks SET next_reminder_at=? WHERE id=?",
                    ((next_r + bump).isoformat(), tid)
                )
            # Send reminder info after reminder
            send_reminder_info(chat_id, user_task_id, remind_at, qi, app.bot, loop)

    conn.commit()
    conn.close()

# --- Task Action Callback Handler ---
async def task_action_handler(update, context):
    query = update.callback_query
    log_debug_event(
        event_type="callback",
        title="task_action_handler",
        msg=query.data,
        userid=query.from_user.id,
        chatid=query.message.chat_id,
    )
    await query.answer()
    data = query.data
    parts = data.split("|")
    if len(parts) != 3 or parts[0] != "taskact":
        return
    action, tid = parts[1], int(parts[2])
    # Fetch task info if needed
    if action == "snooze":
        # For demo, snooze 10 minutes
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute("SELECT next_reminder_at FROM tasks WHERE id=?", (tid,))
            row = cur.fetchone()
            if row and row[0]:
                next_r = safe_parse(row[0]) or datetime.now()
                new_time = next_r + timedelta(minutes=10)
                conn.execute(
                    "UPDATE tasks SET next_reminder_at=?, next_question_at=? WHERE id=?",
                    (new_time.isoformat(), new_time.isoformat(), tid)
                )
                conn.commit()
                await query.edit_message_text("🔕 Snoozed for 10 minutes.")
    elif action == "dismiss":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE tasks SET question_enabled=0, question_interval=0 WHERE id=?", (tid,))
            conn.commit()
            await query.edit_message_text("🔕 Reminders/questions dismissed for this task.")
    elif action == "reenable":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE tasks SET question_enabled=1 WHERE id=?", (tid,))
            conn.commit()
            await query.edit_message_text("🔔 Reminders/questions re-enabled for this task.")
    elif action == "edit":
        # Start edit wizard for this task
        context.user_data.clear()
        context.user_data['edit_task_id'] = tid
        await query.edit_message_text("✏️ Let's edit this task. Please enter the new description (or type /skip to keep current):")
        return EDIT_DESC
    elif action == "done":
        mark_done(tid, admin=True)
        await query.edit_message_text("✅ Task marked as done.")

# --- Edit Wizard States ---
EDIT_DESC, EDIT_DATE, EDIT_TIME, EDIT_TOPIC, EDIT_SUBJECT, EDIT_INTERVAL, EDIT_CONFIRM = range(100, 107)

# --- Edit Wizard Debug Logger ---
import os
EDIT_WIZARD_DEBUG_LOG = os.path.join(BASE_DIR, "edit_wizard_debug.log")
def log_edit_wizard_step(step, info):
    with open(EDIT_WIZARD_DEBUG_LOG, "a") as f:
        from datetime import datetime
        f.write(f"[{datetime.now().isoformat()}] {step}: {info}\n")

# --- Edit Wizard Step Handlers with file logging and correct state transitions ---
async def edit_desc(update, context):
    log_edit_wizard_step("edit_desc", f"called, text='{update.message.text.strip()}'")
    text = update.message.text.strip()
    context.user_data['edit_desc'] = text
    log_edit_wizard_step("edit_desc", f"got text='{text}', next=EDIT_DATE")
    await update.message.reply_text("Enter new due date (or /skip):")
    return EDIT_DATE

async def edit_desc_skip(update, context):
    log_edit_wizard_step("edit_desc_skip", "called")
    context.user_data['edit_desc'] = None
    await update.message.reply_text("Step skipped.")
    log_edit_wizard_step("edit_desc_skip", "next=EDIT_DATE")
    await update.message.reply_text("Enter new due date (or /skip):")
    return EDIT_DATE

async def edit_date(update, context):
    log_edit_wizard_step("edit_date", f"called, text='{update.message.text.strip()}'")
    text = update.message.text.strip()
    dt = dateparser.parse(text)
    if not dt:
        log_edit_wizard_step("edit_date", f"could not parse '{text}'")
        await update.message.reply_text("❌ Could not parse date. Enter again or /skip:")
        return EDIT_DATE
    context.user_data['edit_date'] = dt.date()
    log_edit_wizard_step("edit_date", f"got date={dt.date()}, next=EDIT_TIME")
    await update.message.reply_text("Enter new time (or /skip):")
    return EDIT_TIME

async def edit_date_skip(update, context):
    log_edit_wizard_step("edit_date_skip", "called")
    context.user_data['edit_date'] = None
    await update.message.reply_text("Step skipped.")
    log_edit_wizard_step("edit_date_skip", "next=EDIT_TIME")
    await update.message.reply_text("Enter new time (or /skip):")
    return EDIT_TIME

async def edit_time(update, context):
    log_edit_wizard_step("edit_time", f"called, text='{update.message.text.strip()}'")
    text = update.message.text.strip()
    dt = dateparser.parse(text)
    if not dt or not dt.time():
        log_edit_wizard_step("edit_time", f"could not parse '{text}'")
        await update.message.reply_text("❌ Could not parse time. Enter again or /skip:")
        return EDIT_TIME
    context.user_data['edit_time'] = dt.time()
    log_edit_wizard_step("edit_time", f"got time={dt.time()}, next=EDIT_TOPIC")
    await update.message.reply_text("Enter new topic (or /skip):")
    return EDIT_TOPIC

async def edit_time_skip(update, context):
    log_edit_wizard_step("edit_time_skip", "called")
    context.user_data['edit_time'] = None
    await update.message.reply_text("Step skipped.")
    log_edit_wizard_step("edit_time_skip", "next=EDIT_TOPIC")
    await update.message.reply_text("Enter new topic (or /skip):")
    return EDIT_TOPIC

async def edit_topic(update, context):
    log_edit_wizard_step("edit_topic", f"called, text='{update.message.text.strip()}'")
    text = update.message.text.strip()
    context.user_data['edit_topic'] = text
    log_edit_wizard_step("edit_topic", f"got topic='{text}', next=EDIT_SUBJECT")
    await update.message.reply_text("Enter new subject (or /skip):")
    return EDIT_SUBJECT

async def edit_topic_skip(update, context):
    log_edit_wizard_step("edit_topic_skip", "called")
    context.user_data['edit_topic'] = None
    await update.message.reply_text("Step skipped.")
    log_edit_wizard_step("edit_topic_skip", "next=EDIT_SUBJECT")
    await update.message.reply_text("Enter new subject (or /skip):")
    return EDIT_SUBJECT

async def edit_subject(update, context):
    log_edit_wizard_step("edit_subject", f"called, text='{update.message.text.strip()}'")
    text = update.message.text.strip()
    context.user_data['edit_subject'] = text
    log_edit_wizard_step("edit_subject", f"got subject='{text}'")
    tid = context.user_data['edit_task_id']
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT remind_at, question_interval FROM tasks WHERE id=?", (tid,))
        row = cur.fetchone()
        remind_at, old_interval = row if row else (None, 0)
    if context.user_data.get('edit_date') and context.user_data.get('edit_time'):
        from datetime import datetime as dt
        due_dt = dt.combine(context.user_data['edit_date'], context.user_data['edit_time'])
    elif remind_at:
        due_dt = dateparser.parse(remind_at)
    else:
        due_dt = None
    if due_dt:
        log_edit_wizard_step("edit_subject", f"due_dt={due_dt}, next=EDIT_INTERVAL")
        intervals = get_dynamic_intervals(due_dt)
        keyboard = build_interval_keyboard(intervals)
        await update.message.reply_text(
            f"Enter new reminder interval (or /skip to keep current: {old_interval} min):",
            reply_markup=keyboard
        )
        context.user_data['old_interval'] = old_interval
        return EDIT_INTERVAL
    else:
        log_edit_wizard_step("edit_subject", "no due_dt, next=edit_confirm")
        return await edit_confirm(update, context)

async def edit_subject_skip(update, context):
    log_edit_wizard_step("edit_subject_skip", "called")
    context.user_data['edit_subject'] = None
    await update.message.reply_text("Step skipped.")
    tid = context.user_data['edit_task_id']
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT remind_at, question_interval FROM tasks WHERE id=?", (tid,))
        row = cur.fetchone()
        remind_at, old_interval = row if row else (None, 0)
    if context.user_data.get('edit_date') and context.user_data.get('edit_time'):
        from datetime import datetime as dt
        due_dt = dt.combine(context.user_data['edit_date'], context.user_data['edit_time'])
    elif remind_at:
        due_dt = dateparser.parse(remind_at)
    else:
        due_dt = None
    if due_dt:
        log_edit_wizard_step("edit_subject_skip", f"due_dt={due_dt}, next=EDIT_INTERVAL")
        intervals = get_dynamic_intervals(due_dt)
        keyboard = build_interval_keyboard(intervals)
        await update.message.reply_text(
            f"Enter new reminder interval (or /skip to keep current: {old_interval} min):",
            reply_markup=keyboard
        )
        context.user_data['old_interval'] = old_interval
        return EDIT_INTERVAL
    else:
        log_edit_wizard_step("edit_subject_skip", "no due_dt, next=edit_confirm")
        return await edit_confirm(update, context)

async def edit_interval(update, context):
    log_edit_wizard_step("edit_interval", f"called, text='{update.message.text.strip().lower()}'")
    text = update.message.text.strip().lower()
    mins = parse_interval_label(text)
    if mins is None:
        log_edit_wizard_step("edit_interval", f"could not parse '{text}'")
        await update.message.reply_text("❌ Please select a valid interval or type 'off' or /skip.")
        return EDIT_INTERVAL
    context.user_data['edit_interval'] = mins
    log_edit_wizard_step("edit_interval", f"got interval={mins}, next=edit_confirm")
    return await edit_confirm(update, context)

async def edit_interval_skip(update, context):
    log_edit_wizard_step("edit_interval_skip", "called")
    context.user_data['edit_interval'] = None
    await update.message.reply_text("Step skipped.")
    log_edit_wizard_step("edit_interval_skip", "next=edit_confirm")
    return await edit_confirm(update, context)

async def edit_confirm(update, context, is_query=False):
    tid = context.user_data['edit_task_id']
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT description, remind_at, topic, subject, question_interval FROM tasks WHERE id=?", (tid,))
        row = cur.fetchone()
        old_desc, old_due, old_topic, old_subject, old_interval = row if row else ("", "", "", "", 0)
    desc = context.user_data.get('edit_desc') or old_desc
    due = context.user_data.get('edit_date') or (dateparser.parse(old_due).date() if old_due else None)
    time = context.user_data.get('edit_time') or (dateparser.parse(old_due).time() if old_due else None)
    topic = context.user_data.get('edit_topic') or old_topic
    subject = context.user_data.get('edit_subject') or old_subject
    interval = context.user_data.get('edit_interval')
    if interval is None:
        interval = context.user_data.get('old_interval', old_interval)
    summary = (
        f"Description: {desc}\nDue: {due} {time}\nTopic: {topic or '—'}\nSubject: {subject or '—'}\nInterval: {str(interval) + ' min' if interval else 'No reminders'}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="editconfirm"),
         InlineKeyboardButton("❌ Cancel", callback_data="editcancel")]
    ])
    if is_query:
        await update.message.reply_text(f"Please confirm your edits:\n\n{summary}", reply_markup=keyboard)
    else:
        await update.message.reply_text(f"Please confirm your edits:\n\n{summary}", reply_markup=keyboard)
    return EDIT_CONFIRM

# In edit_confirm_cb, update interval if changed
async def edit_confirm_cb(update, context):
    query = update.callback_query
    if query.data == "editconfirm":
        tid = context.user_data['edit_task_id']
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute("SELECT description, remind_at, topic, subject, question_interval, user_task_id, chat_id FROM tasks WHERE id=?", (tid,))
            row = cur.fetchone()
            old_desc, old_due, old_topic, old_subject, old_interval, user_task_id, chat_id = row if row else ("", "", "", "", 0, 0, 0)
        desc = context.user_data.get('edit_desc') or old_desc
        due = context.user_data.get('edit_date') or (dateparser.parse(old_due).date() if old_due else None)
        time = context.user_data.get('edit_time') or (dateparser.parse(old_due).time() if old_due else None)
        topic = context.user_data.get('edit_topic') or old_topic
        subject = context.user_data.get('edit_subject') or old_subject
        interval = context.user_data.get('edit_interval')
        if interval is None:
            interval = context.user_data.get('old_interval', old_interval)
        # Update DB
        if due and time:
            from datetime import datetime as dt
            remind_at = dt.combine(due, time).isoformat()
        else:
            remind_at = old_due
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE tasks SET description=?, remind_at=?, topic=?, subject=?, question_interval=? WHERE id=?", (desc, remind_at, topic, subject, interval, tid))
            conn.commit()
        # Fetch interval for this task
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute("SELECT remind_at, question_interval, user_task_id, chat_id FROM tasks WHERE id=?", (tid,))
            row = cur.fetchone()
        remind_at, interval, user_task_id, chat_id = row if row else (None, 0, 0, 0)
        # Calculate reminder info
        if remind_at and interval and interval > 0:
            due_dt = dateparser.parse(remind_at)
            now = dt.now()
            mins_until_due = int((due_dt - now).total_seconds() // 60)
            num_reminders = max(1, mins_until_due // interval)
            next_reminder = now + timedelta(minutes=interval)
            await query.edit_message_text(
                f"Task updated!\nYou will receive about {num_reminders} reminders.\nNext reminder: {next_reminder.strftime('%Y-%m-%d %H:%M')}")
        else:
            await query.edit_message_text("Task updated!\nNo reminders will be sent.")
        # Send reminder info after update
        send_reminder_info(chat_id, user_task_id, remind_at, interval, context.bot, asyncio.get_event_loop())
    else:
        await query.edit_message_text("Edit cancelled.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        from telegram.error import BadRequest
        if not (isinstance(e, BadRequest) and "Message is not modified" in str(e)):
            raise
    context.user_data.clear()
    return ConversationHandler.END

# Register edit wizard ConversationHandler
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(task_action_handler, pattern=r"^taskact\|edit\|")],
        states={
            EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_desc), CommandHandler("skip", edit_desc_skip)],
            EDIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_date), CommandHandler("skip", edit_date_skip)],
            EDIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_time), CommandHandler("skip", edit_time_skip)],
            EDIT_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_topic), CommandHandler("skip", edit_topic_skip)],
            EDIT_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_subject), CommandHandler("skip", edit_subject_skip)],
            EDIT_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_interval), CommandHandler("skip", edit_interval_skip), CallbackQueryHandler(interval_button_edit_handler)],
            EDIT_CONFIRM: [CallbackQueryHandler(edit_confirm_cb, pattern=r"^edit(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )
    app.add_handler(edit_conv)

# Helper to check if a user is admin by username or user_id

def is_admin_user(update):
    user_id = update.effective_user.id
    return is_admin_logged_in(user_id)

# Helper: check if user is blocked (DB)
def is_user_blocked(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,))
    blocked = cur.fetchone() is not None
    conn.close()
    return blocked

# Helper: check if user is admin logged in (DB)
def is_admin_logged_in(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT 1 FROM admin_sessions WHERE user_id=?", (user_id,))
    logged_in = cur.fetchone() is not None
    conn.close()
    return logged_in

# Admin login: add to admin_sessions table
def admin_login(user_id, username):
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO admin_sessions (user_id, username, login_time) VALUES (?, ?, ?)",
        (user_id, username, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

# Admin logout: remove from admin_sessions table
def admin_logout(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM admin_sessions WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

# Block user: add to blocked_users table
def block_user(user_id):
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO blocked_users (user_id, blocked_at) VALUES (?, ?)",
        (user_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

# Unblock user: remove from blocked_users table
def unblock_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM blocked_users WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def migrate_legacy_tasks():
    conn = sqlite3.connect(DB_PATH)
    # Set user_id = chat_id where user_id is NULL (legacy private chat tasks)
    conn.execute("UPDATE tasks SET user_id = chat_id WHERE user_id IS NULL")
    conn.commit()
    conn.close()

@block_check
async def migrate_legacy_tasks_cmd(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/migrate_legacy_tasks",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    user_id = update.effective_user.id
    if not is_admin_logged_in(user_id):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    migrate_legacy_tasks()
    await update.message.reply_text("✅ Legacy tasks migrated: user_id set to chat_id where missing.")

@block_check
async def aadd(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="admin_command",
        title="/aadd",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    if not is_admin_user(update):
        return await update.message.reply_text("❌ You must be an admin to use this command.")
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text(
            "Usage: /aadd USER_ID [topic=TOPIC] [subject=SUBJECT] DESCRIPTION at YYYY-MM-DD HH:MM",
            parse_mode="HTML"
        )
    user_id = int(args[0])
    text = " ".join(args[1:])
    import re
    topic = None
    subject = None
    topic_match = re.search(r"topic=([^\]]+)", text)
    subject_match = re.search(r"subject=([^\]]+)", text)
    if topic_match:
        topic = topic_match.group(1).strip()
        text = re.sub(r"topic=[^\]]+", "", text)
    if subject_match:
        subject = subject_match.group(1).strip()
        text = re.sub(r"subject=[^\]]+", "", text)
    text = text.strip()
    if " at " not in text:
        return await update.message.reply_text(
            "❌ Usage: /aadd USER_ID [topic=TOPIC] [subject=SUBJECT] DESCRIPTION at YYYY-MM-DD HH:MM",
            parse_mode="HTML"
        )
    desc, _, timestr = text.rpartition(" at ")
    from dateparser import parse as parse_date
    dt = parse_date(timestr)
    if not dt:
        return await update.message.reply_text("❌ Could not parse date/time.")
    chat_id = update.effective_chat.id
    task_id, user_task_id = add_task(chat_id, user_id, desc, dt, topic, subject)
    set_question_prefs(task_id, 0, 0) # Default to off for new tasks
    details = f"_" + desc + "_"
    if topic:
        details = f"[Topic: {topic}] " + details
    if subject:
        details = f"[Subject: {subject}] " + details
    await update.message.reply_text(
        f"✅ Task *#{user_task_id}* for user {user_id} scheduled: {details} at {dt}",
        parse_mode="Markdown"
    )

# Helper: generate dynamic interval options based on due date

def get_dynamic_intervals(due_dt):
    from datetime import datetime
    now = datetime.now()
    delta = due_dt - now
    minutes = delta.total_seconds() / 60
    days = delta.total_seconds() / 86400
    options = []
    if minutes < 60:
        options = [1, 5, 10, 15, 30]
    elif minutes < 180:
        options = [5, 10, 15, 30, 60]
    elif minutes < 360:
        options = [15, 30, 60, 180]
    elif minutes < 720:
        options = [30, 60, 180, 360]
    elif minutes < 1440:
        options = [60, 180, 360, 720]
    elif days < 3:
        options = [180, 360, 720, 1440]
    elif days < 7:
        options = [360, 720, 1440, 4320]
    elif days < 14:
        options = [720, 1440, 4320, 10080]
    elif days < 30:
        options = [1440, 4320, 10080, 20160]
    elif days < 90:
        options = [4320, 10080, 20160, 43200]
    elif days < 180:
        options = [10080, 20160, 43200, 86400]
    elif days < 365:
        options = [20160, 43200, 86400, 129600]
    else:
        options = [43200, 86400, 129600, 525600]
    return options

# --- Conversation Wizard Handlers ---
async def start_add(update, context):
    print("\n\nDEBUG: start_add called\n\n")  # Debug print to confirm handler is triggered
    text = update.message.text.partition(" ")[2].strip()
    if text:
        desc, dt, topic, subject = parse_power_user_entry(text)
        if desc and dt:
            context.user_data.update({'desc': desc, 'date': dt.date(), 'time': dt.time(), 'topic': topic, 'subject': subject})
            return await ask_interval(update, context)
        else:
            await update.message.reply_text("Could not parse your entry. \nLet's add the task step by step. \n\nYou can use /skip to skip a step, /cancel to cancel the operation.")
    await update.message.reply_text("What is the task description?")
    return TASK_DESC

def parse_power_user_entry(text):
    import re
    topic = subject = None
    topic_match = re.search(r"topic=([^\]]+)", text)
    subject_match = re.search(r"subject=([^\]]+)", text)
    if topic_match:
        topic = topic_match.group(1).strip()
        text = re.sub(r"topic=[^\]]+", "", text)
    if subject_match:
        subject = subject_match.group(1).strip()
        text = re.sub(r"subject=[^\]]+", "", text)
    text = text.strip()
    if " at " in text:
        desc, _, timestr = text.rpartition(" at ")
        dt = dateparser.parse(timestr)
        if dt:
            return desc.strip(), dt, topic, subject
    return None, None, None, None

async def task_desc(update, context):
    desc = update.message.text.strip()
    if not desc:
        await update.message.reply_text("Please enter a task description.")
        return TASK_DESC
    context.user_data['desc'] = desc
    await update.message.reply_text("When is it due? (Send date or pick below)", reply_markup=build_calendar_keyboard())
    return TASK_DATE

async def task_date(update, context):
    text = update.message.text.strip()
    dt = dateparser.parse(text)
    if not dt:
        await update.message.reply_text("❌ Could not understand the date. Please enter a valid date (e.g., 'tomorrow', '2025-07-15') or pick from the calendar.")
        return TASK_DATE
    context.user_data['date'] = dt.date()
    await update.message.reply_text("What time? (e.g., 18:00 or pick below)", reply_markup=build_time_keyboard())
    return TASK_TIME

async def calendar_handler(update, context):
    query = update.callback_query
    log_debug_event(
        event_type="callback",
        title="calendar_handler",
        msg=query.data,
        userid=query.from_user.id,
        chatid=query.message.chat_id,
    )
    try:
        print("\n\nDEBUG: calendar_handler called with data:", update.callback_query.data)
        data = query.data
        parts = data.split("|")
        print("\nDEBUG: parts =", parts)
        if len(parts) == 4 and all(p.isdigit() for p in parts[1:]):
            print("\nDEBUG: Valid date parts, proceeding to next step.")
            _, y, m, d = parts
            from datetime import date
            context.user_data['date'] = date(int(y), int(m), int(d))
            print("DEBUG: query.message.chat_id =", query.message.chat_id)
            print("DEBUG: query.message.message_id =", query.message.message_id)
            print("DEBUG: query.from_user.id =", query.from_user.id)
            print("DEBUG: context.user_data =", context.user_data)
            try:
                await query.edit_message_text(f"Date selected: {context.user_data['date']}")
                print("\nDEBUG: edit_message_text succeeded")
            except Exception as e:
                print("\nERROR: edit_message_text failed:", e)
                await query.message.reply_text(f"Date selected: {context.user_data['date']}")
                print("DEBUG: reply_text sent (date selected)")
            await query.message.reply_text("What time? (e.g., 18:00 or pick below)", reply_markup=build_time_keyboard())
            print("\nDEBUG: Sent time picker")
            return TASK_TIME
        else:
            print("\nDEBUG: Invalid date parts or custom picker, staying in TASK_DATE.")
            await query.answer("Custom date picker not implemented. Please type your date.")
            return TASK_DATE
    except Exception as e:
        print("\nERROR in calendar_handler:", e)
        raise

async def task_time(update, context):
    text = update.message.text.strip()
    dt = dateparser.parse(text)
    if not dt or not dt.time():
        await update.message.reply_text("❌ Please enter a valid time (e.g., '18:00', '8pm') or pick from the buttons.")
        return TASK_TIME
    context.user_data['time'] = dt.time()
    await update.message.reply_text("Add a topic? (optional, or type /skip to skip)")
    return TASK_TOPIC

async def time_button_handler(update, context):
    query = update.callback_query
    log_debug_event(
        event_type="callback",
        title="time_button_handler",
        msg=query.data,
        userid=query.from_user.id,
        chatid=query.message.chat_id,
    )
    data = query.data
    if data.startswith("time|"):
        _, t = data.split("|")
        from datetime import time as dtime
        h, m = map(int, t.split(":"))
        context.user_data['time'] = dtime(h, m)
        await query.edit_message_text(f"Time selected: {t}")
        await query.message.reply_text("Add a topic? (optional, or type /skip to skip)")
        return TASK_TOPIC
    return TASK_TIME

async def time_skip_handler(update, context):
    context.user_data['time'] = None  # Or set a default time if you want
    await update.message.reply_text("Step skipped.")
    await update.message.reply_text("Add a topic? (optional, or type /skip to skip)")
    return TASK_TOPIC

async def task_topic(update, context):
    text = update.message.text.strip()
    if text.lower() == '/skip':
        context.user_data['topic'] = None
    else:
        context.user_data['topic'] = text
    await update.message.reply_text("Add a subject? (optional, or type /skip to skip)")
    return TASK_SUBJECT

async def topic_skip_handler(update, context):
    context.user_data['topic'] = None
    await update.message.reply_text("Step skipped.")
    await update.message.reply_text("Add a subject? (optional, or type /skip to skip)")
    return TASK_SUBJECT

async def task_subject(update, context):
    text = update.message.text.strip()
    if text.lower() == '/skip':
        context.user_data['subject'] = None
    else:
        context.user_data['subject'] = text
    return await ask_interval(update, context)

async def subject_skip_handler(update, context):
    context.user_data['subject'] = None
    await update.message.reply_text("Step skipped.")
    return await ask_interval(update, context)

async def ask_interval(update, context):
    from datetime import datetime
    due_dt = datetime.combine(context.user_data['date'], context.user_data['time'])
    intervals = get_dynamic_intervals(due_dt)
    keyboard = build_interval_keyboard(intervals)
    await update.message.reply_text("Set a reminder/question interval:", reply_markup=keyboard)
    return TASK_INTERVAL

async def task_interval(update, context):
    text = update.message.text.strip().lower()
    mins = parse_interval_label(text)
    if mins is None:
        await update.message.reply_text("❌ Please select a valid interval or type 'off'.")
        return TASK_INTERVAL
    context.user_data['interval'] = mins
    return await show_task_summary(update, context)

async def interval_button_handler(update, context):
    query = update.callback_query
    log_debug_event(
        event_type="callback",
        title="interval_button_handler",
        msg=query.data,
        userid=query.from_user.id,
        chatid=query.message.chat_id,
    )
    data = query.data
    if data.startswith("interval|"):
        _, mins = data.split("|")
        context.user_data['interval'] = int(mins)
        await query.edit_message_text(f"Reminding interval selected: {mins} min" if int(mins) > 0 else "Reminders off.")
        return await show_task_summary(query, context, is_query=True)
    return TASK_INTERVAL

async def show_task_summary(update, context, is_query=False):
    data = context.user_data
    summary = (
        f"Description: {data['desc']}\n"
        f"Due: {data['date']} {data['time']}\n"
        f"Topic: {data.get('topic') or '—'}\n"
        f"Subject: {data.get('subject') or '—'}\n"
        f"Interval: {str(data['interval']) + ' min' if data['interval'] else 'No reminders'}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])
    if is_query:
        await update.message.reply_text(f"Please confirm your task:\n\n{summary}", reply_markup=keyboard)
    else:
        await update.message.reply_text(f"Please confirm your task:\n\n{summary}", reply_markup=keyboard)
    return TASK_CONFIRM

async def confirm_handler(update, context):
    query = update.callback_query
    log_debug_event(
        event_type="callback",
        title="confirm_handler",
        msg=query.data,
        userid=query.from_user.id,
        chatid=query.message.chat_id,
    )
    if query.data == "confirm":
        data = context.user_data
        from datetime import datetime
        due_dt = datetime.combine(data['date'], data['time'])
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        task_id, user_task_id = add_task(chat_id, user_id, data['desc'], due_dt, data.get('topic'), data.get('subject'))
        set_question_prefs(task_id, data['interval'], 1 if data['interval'] > 0 else 0)
        # Calculate reminder info
        interval = data['interval']
        now = datetime.now()
        if interval > 0:
            mins_until_due = int((due_dt - now).total_seconds() // 60)
            num_reminders = max(1, mins_until_due // interval)
            next_reminder = now + timedelta(minutes=interval)
            await query.edit_message_text(
                f"Task added! 🎉\nYou will receive about {num_reminders} reminders.\nNext reminder: {next_reminder.strftime('%Y-%m-%d %H:%M')}")
        else:
            await query.edit_message_text("Task added! 🎉\nNo reminders will be sent.")
    else:
        await query.edit_message_text("🏁 Task creation cancelled. \n\nℹ️ /menu")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        from telegram.error import BadRequest
        if not (isinstance(e, BadRequest) and "Message is not modified" in str(e)):
            raise
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update, context):
    # Determine if this is an edit or add wizard
    if context.user_data.get('edit_task_id'):
        await update.message.reply_text("🏁 Task edit cancelled.\n\nℹ️ /menu")
    else:
        await update.message.reply_text("🏁 Task creation cancelled.\n\nℹ️ /menu")
    context.user_data.clear()
    return ConversationHandler.END

# --- Helper Keyboards ---
def build_calendar_keyboard():
    # For demo: just today/tomorrow/other. Replace with real calendar picker for production.
    from datetime import date, timedelta
    today = date.today()
    tomorrow = today + timedelta(days=1)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Today ({today})", callback_data=f"calendar|{today.year}|{today.month}|{today.day}"),
         InlineKeyboardButton(f"Tomorrow ({tomorrow})", callback_data=f"calendar|{tomorrow.year}|{tomorrow.month}|{tomorrow.day}")],
        [InlineKeyboardButton("Pick another date", callback_data="calendar|pick|pick|pick")]
    ])
    return keyboard

def build_time_keyboard():
    # Common times
    times = ["09:00", "12:00", "15:00", "18:00", "21:00"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t, callback_data=f"time|{t}") for t in times]
    ])
    return keyboard

def build_interval_keyboard(intervals):
    # intervals: list of minutes
    row = []
    for i in intervals:
        if i < 60:
            label = f"{i} min"
        elif i < 1440:
            label = f"{i//60} hr"
        elif i < 10080:
            label = f"{i//1440} day"
        elif i < 43200:
            label = f"{i//10080} wk"
        elif i < 525600:
            label = f"{i//43200} mo"
        else:
            label = f"{i//525600} yr"
        row.append(InlineKeyboardButton(label, callback_data=f"interval|{i}"))
    keyboard = InlineKeyboardMarkup([row, [InlineKeyboardButton("off", callback_data="interval|0")]])
    return keyboard

# — Main Entrypoint —
def main():
    print("\n\nDEBUG: main() called\n\n")  # Debug print to confirm main() is being called
    init_db()
    app = ApplicationBuilder().token(config.TELEGRAM_TOKEN).build()
    import asyncio
    loop = asyncio.get_event_loop()  # Store the main event loop

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', start_add)],
        states={
            TASK_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_desc)],
            TASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_date),
                        CallbackQueryHandler(calendar_handler, pattern=r"^calendar\|")],
            TASK_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, task_time),
                CallbackQueryHandler(time_button_handler),
                CommandHandler("skip", time_skip_handler),
            ],
            TASK_TOPIC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, task_topic),
                CommandHandler("skip", topic_skip_handler),
                CallbackQueryHandler(topic_skip_handler, pattern=r"^skip$")
            ],
            TASK_SUBJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, task_subject),
                CommandHandler("skip", subject_skip_handler),
                CallbackQueryHandler(subject_skip_handler, pattern=r"^skip$")
            ],
            TASK_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_interval),
                            CallbackQueryHandler(interval_button_handler)],
            TASK_CONFIRM: [CallbackQueryHandler(confirm_handler, pattern=r"^(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    app.add_handler(conv_handler)

    # Register edit wizard ConversationHandler (edit_conv) immediately after conv_handler
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(task_action_handler, pattern=r"^taskact\|edit\|"), CommandHandler("edit", edit_wizard_entry)],
        states={
            EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_desc), CommandHandler("skip", edit_desc_skip)],
            EDIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_date), CommandHandler("skip", edit_date_skip)],
            EDIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_time), CommandHandler("skip", edit_time_skip)],
            EDIT_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_topic), CommandHandler("skip", edit_topic_skip)],
            EDIT_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_subject), CommandHandler("skip", edit_subject_skip)],
            EDIT_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_interval), CommandHandler("skip", edit_interval_skip), CallbackQueryHandler(interval_button_edit_handler)],
            EDIT_CONFIRM: [CallbackQueryHandler(edit_confirm_cb, pattern=r"^edit(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )
    app.add_handler(edit_conv)

    # Register task action handler for inline buttons right after the wizards
    app.add_handler(CallbackQueryHandler(task_action_handler, pattern=r"^taskact\|"))

    # Now register all other command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", listall))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(question_interval_cb, pattern=r"^qi\\|"))
    app.add_handler(CommandHandler("edit", edit))
    app.add_handler(CommandHandler("del", delete))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(CommandHandler("alogin", alogin))
    app.add_handler(CommandHandler("alogout", alogout))
    app.add_handler(CommandHandler("menu", slash_menu))
    app.add_handler(CommandHandler("m", admin_menu))
    app.add_handler(CommandHandler("alist", alist))
    app.add_handler(CommandHandler("aedit", aedit))
    app.add_handler(CommandHandler("adone", adone))
    app.add_handler(CommandHandler("aulist", aulist))
    app.add_handler(CommandHandler("adel", adel))
    app.add_handler(CommandHandler("audel", audel))
    app.add_handler(CommandHandler("ablock", ablock))
    app.add_handler(CommandHandler("aunblock", aunblock))
    app.add_handler(CommandHandler("achats", achats))
    app.add_handler(CommandHandler("ausers", ausers))
    app.add_handler(CommandHandler("migrate_legacy_tasks", migrate_legacy_tasks_cmd))
    app.add_handler(CommandHandler("aadd", aadd))

    # Move the generic MessageHandler to the very end
    app.add_handler(MessageHandler(None, interval_reply_handler))

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_reminders, "interval", minutes=1, args=(app, loop))
    scheduler.start()

    app.run_polling()

# Catch-all message logger
async def log_all_messages(update: Update, ctx: CallbackContext):
    user = update.effective_user
    chat = update.effective_chat
    msg = update.message.text if update.message else str(update)
    log_debug_event(
        event_type="message",
        title="User message",
        msg=msg,
        userid=user.id if user else None,
        chatid=chat.id if chat else None,
    )

# In main(), after all other handlers are registered:
# ... existing code ...
    app.add_handler(MessageHandler(filters.ALL, log_all_messages), group=999)
# ... existing code ...

@block_check
async def info_cmd(update: Update, ctx: CallbackContext):
    log_debug_event(
        event_type="command",
        title="/info",
        msg=update.message.text,
        userid=update.effective_user.id,
        chatid=update.effective_chat.id,
    )
    args = ctx.args
    if not args or not args[0].isdigit():
        return await update.message.reply_text(
            "Usage: /info <TASK_ID>\n(TASK_ID is as shown in /list)",
            parse_mode=None
        )
    utid = int(args[0])
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT id, description, remind_at, is_done, topic, subject, question_interval, question_enabled, next_reminder_at FROM tasks WHERE chat_id=? AND user_id=? AND user_task_id=?",
            (chat_id, user_id, utid)
        )
        row = cur.fetchone()
    if not row:
        return await update.message.reply_text("Task not found.")
    tid, desc, remind_at, is_done, topic, subject, interval, enabled, next_reminder_at = row
    status = "✅ Done" if is_done else "🕒 Active"
    due = remind_at if remind_at else "—"
    topic = topic or "—"
    subject = subject or "—"
    interval_str = f"{interval} min" if interval and enabled else "No reminders"
    # Calculate reminders left and next reminder
    if remind_at and interval and enabled:
        from datetime import datetime as dt
        due_dt = dateparser.parse(remind_at)
        now = dt.now()
        mins_until_due = int((due_dt - now).total_seconds() // 60)
        num_reminders = max(1, mins_until_due // interval)
        next_reminder = dateparser.parse(next_reminder_at) if next_reminder_at else None
        next_reminder_str = next_reminder.strftime('%Y-%m-%d %H:%M') if next_reminder else "—"
    else:
        num_reminders = 0
        next_reminder_str = "—"
    summary = (
        f"*Task #{utid}*\n"
        f"Description: {desc}\n"
        f"Due: {due}\n"
        f"Status: {status}\n"
        f"Topic: {topic}\n"
        f"Subject: {subject}\n"
        f"Interval: {interval_str}\n"
        f"Reminders left: {num_reminders}\n"
        f"Next reminder: {next_reminder_str}"
    )
    await update.message.reply_text(summary, parse_mode="Markdown")

# --- Helper: Send reminder info to user ---
def send_reminder_info(chat_id, user_task_id, remind_at, interval, bot, loop):
    from datetime import datetime as dt
    if remind_at and interval and interval > 0:
        due_dt = dateparser.parse(remind_at)
        now = dt.now()
        mins_until_due = int((due_dt - now).total_seconds() // 60)
        num_reminders = max(1, mins_until_due // interval)
        next_reminder = now + timedelta(minutes=interval)
        text = (
            f"ℹ️ You will receive about {num_reminders} reminders for task #{user_task_id}.\n"
            f"Next reminder: {next_reminder.strftime('%Y-%m-%d %H:%M')}"
        )
    else:
        text = f"ℹ️ No reminders will be sent for task #{user_task_id}."
    import asyncio
    asyncio.run_coroutine_threadsafe(
        bot.send_message(chat_id, text),
        loop
    )

# Add back the missing interval_button_edit_handler for edit wizard
async def interval_button_edit_handler(update, context):
    query = update.callback_query
    data = query.data
    if data.startswith("interval|"):
        _, mins = data.split("|")
        context.user_data['edit_interval'] = int(mins)
        await query.edit_message_text(f"Reminding interval selected: {mins} min" if int(mins) > 0 else "Reminders off.")
        return await edit_confirm(query, context, is_query=True)
    return EDIT_INTERVAL

# --- Edit Wizard Entry Point for /edit <TASK_ID> ---
@block_check
async def edit_wizard_entry(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    args = context.args
    if not args or not args[0].isdigit() or len(args) > 1:
        # If fields are provided, fall back to quick edit
        return await edit(update, context)
    utid = int(args[0])
    # Look up the real task id for this user and chat
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM tasks WHERE chat_id=? AND user_id=? AND user_task_id=?", (chat_id, user_id, utid))
    row = cur.fetchone()
    conn.close()
    if not row:
        return await update.message.reply_text("Task not found or you do not have permission to edit it.")
    tid = row[0]
    context.user_data.clear()
    context.user_data['edit_task_id'] = tid
    await update.message.reply_text("✏️ Let's edit this task. Please enter the new description (or type /skip to keep current):")
    return EDIT_DESC

if __name__ == "__main__":
    if os.getenv("DEV"):
        from watchgod import run_process
        run_process(BASE_DIR, target=main)
    else:
        main()
    