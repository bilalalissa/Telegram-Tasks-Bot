# TaskBot Documentation

> **Note:** This file is documentation only. Save it as `README.md` in your project root and do not execute it as Python code.

---

## Overview

TaskBot is a Telegram bot designed to help you manage tasks with:

- Scheduled due-time reminders
- Optional follow-up questions before the due time
- Recurring alarms until tasks are completed
- Per-task topics and subjects
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
    - [**Commands**](#commands)
    - [**Wizard Flows**](#wizard-flows)
    - [**Interval Selection**](#interval-selection)
  - [Error Handling \& Wizard Flows](#error-handling--wizard-flows)
  - [Database Schema](#database-schema)
  - [Development \& Hot-Reload](#development--hot-reload)
  - [Troubleshooting](#troubleshooting)
  - [Next Steps](#next-steps)

---

## Features

- **Add Tasks**: `/add [topic=TOPIC] [subject=SUBJECT] DESCRIPTION at YYYY-MM-DD HH:MM`
- **Step-by-Step Wizard**: `/add` (with no arguments) launches an interactive wizard to collect task details
- **List Tasks**: `/list`
- **Complete Tasks**: `/done <TASK_ID>`
- **Edit Tasks**: `/edit <TASK_ID>` launches a wizard, or `/edit <TASK_ID> desc=... due=...` for quick edits
- **Delete Tasks**: `/del <TASK_ID>`
- **Task Info**: `/info <TASK_ID>`
- **Menu**: `/menu` or `/start` to see all commands
- **Follow-Up Questions**: Bot prompts "Are you still working on...?" at chosen intervals
- **Due-Time Alarms**: Bot sends a reminder at the scheduled time and repeats until completion
- **Automatic Schema Migrations**: `init_db()` creates or updates the SQLite schema
- **Hot-Reload**: Automatic restart on code changes via `watchgod` in development mode

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/task_bot.git
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

### **Commands**

| Command | Description |
|---------|-------------|
| `/start` | Show usage instructions |
| `/menu`  | Show all available commands |
| `/add`   | Add a new task (step-by-step wizard) |
| `/add [topic=TOPIC] [subject=SUBJECT] DESCRIPTION at YYYY-MM-DD HH:MM` | Add a new task in one line |
| `/list`  | List your tasks |
| `/done <TASK_ID>` | Mark a task as done |
| `/edit <TASK_ID>` | Edit a task (wizard) |
| `/edit <TASK_ID> desc=... due=... topic=... subject=...` | Quick edit fields |
| `/del <TASK_ID>` | Delete a task |
| `/info <TASK_ID>` | Show detailed info for a task |

### **Wizard Flows**
- If you use `/add` or `/edit <TASK_ID>` with no further arguments, the bot will guide you through each step.
- At any step, you can use `/skip` to skip the current field, or `/cancel` to cancel the operation.
- After entering the due date and time, you will be prompted to select a reminder/question interval.
- After confirming, the bot will show a summary and schedule reminders.

### **Interval Selection**
- After adding or editing a task, you will be prompted to select a reminder/question interval (e.g., 5 min, 1 hr, off).
- You can always change the interval later by editing the task.

---

## Error Handling & Wizard Flows

- If you provide an incomplete or malformed `/add` command, the bot will:
  - Show a usage message if the format is missing required parts (description, `at`, or date/time).
  - Show a specific error if the date/time is missing or unparseable.
  - Fall back to the step-by-step wizard if needed.
- **Example error messages:**
  - `❌ Usage: /add then hit Enter/return key, and follow up with the steps. Or /add <SUBJECT> <TOPIC> <DESCRIPTION> at YYYY-MM-DD HH:MM Or /skip to skip or /cancel to cancel task.`
  - `❌ Could not parse date/time. Please use a format like YYYY-MM-DD HH:MM.`
- When you use `/skip`, the bot replies: `Step skipped.`
- When you use `/cancel`, the bot replies: `🏁 Task creation cancelled. \n\nℹ️ /menu` or `🏁 Task edit cancelled. \n\nℹ️ /menu`

---

## Database Schema

The SQLite database (`tasks.db`) includes a `tasks` table with the following columns:

| Column               | Type     | Description                                         |
|----------------------|----------|-----------------------------------------------------|
| `id`                 | INTEGER  | Primary key                                         |
| `chat_id`            | INTEGER  | Telegram chat identifier                            |
| `user_id`            | INTEGER  | Telegram user identifier                            |
| `user_task_id`       | INTEGER  | Per-user task number                                |
| `description`        | TEXT     | Task description                                    |
| `remind_at`          | DATETIME | Scheduled reminder time                             |
| `is_done`            | BOOLEAN  | Flag (0 = active, 1 = completed)                    |
| `question_interval`  | INTEGER  | Minutes between follow-up questions                 |
| `question_enabled`   | BOOLEAN  | Flag (1 = enabled, 0 = disabled)                    |
| `next_question_at`   | DATETIME | Timestamp for the next follow-up question           |
| `next_reminder_at`   | DATETIME | Timestamp for the next due-time reminder            |
| `topic`              | TEXT     | Task topic (optional)                               |
| `subject`            | TEXT     | Task subject (optional)                             |

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
- **If you get a usage error:**
  - Make sure your `/add` command includes both a description and a date/time after `at`.
  - Use `/add` alone to launch the step-by-step wizard.

---

## Next Steps

- [x] Add tasks' listing and interaction by user (e.g. connect task/s by user id)
- [x] Add administrative tools: login, list, edit and delete tasks and users
- [x] Update '/add' funtion to include topic/subject
- [x] A form or better/esaier way to add task details with more question reminding options
- [x] Add inline actions (Snooze, Dismiss, Edit, Delete) to tasks and reminders
- [ ] Text search tasks by title/description
- [ ] Filter tasks by topic/subject
- [x] '/menu' to show menu
- [ ] Timeline tasks show/list option
- [ ] Support per-user time zones and localization
- [ ] Dockerize the bot for container-based deployment
- [ ] Migrate to a production-grade database (PostgreSQL, Redis)
- [ ] Build a web dashboard for managing and visualizing tasks

---
