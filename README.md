# TaskBot Documentation

> **Note:** This file is documentation only. Save it as `README.md` in your project root and do not execute it as Python code.

---

## Overview

TaskBot is a Telegram bot designed to help you manage tasks with:

- Scheduled due-time reminders
- Optional follow-up questions before the due time
- Recurring alarms until tasks are completed
- Persistent storage using SQLite with automatic schema migrations
- Development hot-reload with `watchgod`

---

## Table of Contents

- [TaskBot Documentation](#taskbot-documentation)
  - [Overview](#overview)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Usage](#usage)
  - [Database Schema](#database-schema)
  - [Development \& Hot-Reload](#development--hot-reload)
  - [Troubleshooting](#troubleshooting)
  - [Next Steps](#next-steps)

---

## Features

- **Add Tasks**: `/add DESCRIPTION at YYYY-MM-DD HH:MM`
- **List Tasks**: `/list`
- **Complete Tasks**: `/done TASK_ID`
- **Follow-Up Questions**: Bot prompts "Are you still working on...?" at chosen intervals
- **Due-Time Alarms**: Bot sends a reminder at the scheduled time and repeats until completion
- **Automatic Schema Migrations**: `init_db()` creates or updates the SQLite schema
- **Hot-Reload**: Automatic restart on code changes via `watchgod` in development mode

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/bilalalissa/Telegram-Tasks-Bot.git
   cd task_bot
   ```
2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv venv
   # macOS/Linux
   source venv/bin/activate
   # Windows PowerShell
   .\venv\Scripts\Activate.ps1
   ```
3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Create a `.env` file** with your bot token:
   ```dotenv
   TELEGRAM_TOKEN=<YOUR_TELEGRAM_BOT_TOKEN>
   ```

---

## Configuration

| Environment Variable | Description             |
|----------------------|-------------------------|
| `TELEGRAM_TOKEN`     | Telegram Bot API token  |

Add additional environment variables here as needed.

---

## Usage

- **Start the bot**:
  ```bash
  python bot.py           # production mode
  DEV=1 python bot.py     # development mode with hot-reload
  ```
- **Commands**:
  - `/start` — Show usage instructions
  - `/add DESCRIPTION at YYYY-MM-DD HH:MM` — Schedule a new task
  - `/list` — Display active tasks
  - `/done TASK_ID` — Mark a task as complete
- **Inline Prompts**:
  - After `/add`, select an interval (5 min, 15 min, 1 hr) or disable follow-up questions

---

## Database Schema

The SQLite database (`tasks.db`) includes a `tasks` table with the following columns:

| Column               | Type     | Description                                         |
|----------------------|----------|-----------------------------------------------------|
| `id`                 | INTEGER  | Primary key                                         |
| `chat_id`            | INTEGER  | Telegram chat identifier                            |
| `description`        | TEXT     | Task description                                    |
| `remind_at`          | DATETIME | Scheduled reminder time                             |
| `is_done`            | BOOLEAN  | Flag (0 = active, 1 = completed)                    |
| `question_interval`  | INTEGER  | Minutes between follow-up questions                 |
| `question_enabled`   | BOOLEAN  | Flag (1 = enabled, 0 = disabled)                    |
| `next_question_at`   | DATETIME | Timestamp for the next follow-up question           |
| `next_reminder_at`   | DATETIME | Timestamp for the next due-time reminder            |

The `init_db()` function in `bot.py` automatically creates or migrates this schema on startup.

---

## Development & Hot-Reload

To enable automatic restarts on code changes, run:
```bash
DEV=1 python bot.py
```
This uses the `watchgod` library to watch for file changes and restart the bot process.

---

## Troubleshooting

- **Do not run README.md as Python code**
- **Missing Dependencies**: Run `pip install -r requirements.txt`
- **Database Issues**: Delete `tasks.db` to reset the schema and rerun the bot
- **Scheduler Logs**: Check for `🔎 check_reminders` log entries every minute
- **Date Parsing**: Use valid formats (`YYYY-MM-DD HH:MM`) or natural language parseable by `dateparser`

---

## Next Steps

- Add tasks' listing and interaction by user (e.g. connect task/s by user id)
- Support per-user time zones and localization
- '/add' funtion to include topic/subject 
- Add inline actions (Snooze, Dismiss, Edit, Export) to reminders
- Text search tasks by title/description
- Filter tasks by topic/subject
- '/' to show menu
- Timeline tasks show/list option
- A form or better/esaier way to enter task details
- Add administrative tools: list all tasks, edit and delete
- Dockerize the bot for container-based deployment
- Migrate to a production-grade database (PostgreSQL, Redis)
- Build a web dashboard for managing and visualizing tasks

---

*Documentation only — do not execute.*
