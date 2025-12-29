# Doctor Appointment Watcher

A simple SaaS application to monitor navstevalekara.sk for available doctor appointment slots and receive Telegram notifications.

## Features

- 🔍 Monitor specific doctors for appointment availability
- 📅 Watch multiple target dates
- 📱 Telegram notifications when slots become available
- 🔗 Unique shareable URLs for each watcher
- ⚡ Background checking every 5 minutes
- 🚫 No duplicate notifications

## Quick Start

### Local Development

1. Install dependencies with `uv`:
```bash
uv pip install -e .
```

2. Run the application:
```bash
uvicorn app:app --reload
```

3. Open http://localhost:8000

### Docker Deployment

1. Build and run with docker-compose:
```bash
docker-compose up -d
```

2. Access the app at http://localhost:8000

3. View logs:
```bash
docker-compose logs -f
```

## How It Works

1. **Create a Watcher**: Fill in the form with doctor details, target dates, and Telegram credentials
2. **Get Unique URL**: Each watcher gets a unique URL you can bookmark or share
3. **Automatic Checking**: Every 5 minutes, the app checks for available slots on your target dates
4. **Telegram Notifications**: When new slots are found, you get a notification (no duplicates)
5. **Manage**: View status, toggle active/inactive, or delete watchers anytime

## Configuration

### Finding the Doctor Code

1. Go to navstevalekara.sk and find your doctor
2. Open browser DevTools (F12) → Network tab
3. Click on any date/week in the calendar
4. Look for the `order.php` request
5. The `dc` parameter is the doctor code (e.g., `15313`)

### Setting Up Telegram

1. Create a bot with [@BotFather](https://t.me/botfather)
2. Get the bot token (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
3. Create a channel or group
4. Add your bot to the channel/group
5. Get the chat ID (use [@userinfobot](https://t.me/userinfobot) or check bot updates)

## Project Structure

```
navstevalekara-notif/
├── app.py                 # Main application (FastAPI + SQLAlchemy + APScheduler)
├── templates/
│   ├── index.html        # Create watcher form
│   └── watcher.html      # View watcher status
├── pyproject.toml        # Dependencies
├── Dockerfile            # Docker image
├── docker-compose.yml    # Docker deployment
└── watchers.db           # SQLite database (auto-created)
```

## Tech Stack

- **Backend**: FastAPI
- **Database**: SQLite
- **Background Jobs**: APScheduler
- **Frontend**: HTML + CSS (no JavaScript framework)
- **Deployment**: Docker
- **Dependency Management**: uv

## Database

Two simple tables:

- `watchers` - Stores watcher configuration
- `notified_slots` - Prevents duplicate notifications

## License

MIT
