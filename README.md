# Doctor Appointment Watcher

A simple SaaS application to monitor navstevalekara.sk for available doctor appointment slots and receive notifications via Telegram or Email.

## Features

- 🔍 Monitor specific doctors for appointment availability
- 📅 Watch multiple target dates
- 📱 **Telegram** OR 📧 **Email** notifications when slots become available
- 🎨 Rich HTML email templates (when using Email notifications)
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

1. **Create a Watcher**: Fill in the form with doctor details, target dates, and choose notification type (Telegram OR Email)
2. **Get Unique URL**: Each watcher gets a unique URL you can bookmark or share
3. **Automatic Checking**: Every 5 minutes, the app checks for available slots on your target dates
4. **Notifications**: When new slots are found, you get a notification via your chosen method (no duplicates)
5. **Manage**: View status, toggle active/inactive, or delete watchers anytime

## Configuration

### Getting the Doctor URL

Simply copy the full URL from your doctor's page on navstevalekara.sk. The app will automatically extract the doctor code.

Example: `https://www.navstevalekara.sk/lekari/gynekolog-gynekologia-s11003/bratislavsky-kraj-k300/bratislava-5-o504/petrzalka-m1015/mudr-radmila-sladicekova-phd-mph-d15313.html`

### Setting Up Email Notifications (Mailjet)

To enable email notifications, you need to configure Mailjet:

1. Create a free account at [https://www.mailjet.com/](https://www.mailjet.com/)
2. Get your API credentials from the Mailjet dashboard
3. Create a `.env` file in the project root:

```env
MAILJET_API_KEY=your_api_key_here
MAILJET_SECRET_KEY=your_secret_key_here
MAILJET_SENDER_EMAIL=noreply@yourdomain.com
MAILJET_SENDER_NAME=Doctor Appointment Watcher
```

4. Restart the application

**Note**: If Mailjet is not configured, only Telegram notifications will be available. Email notifications use beautiful HTML templates with appointment details.

### Setting Up Telegram Notifications

1. Create a bot with [@BotFather](https://t.me/botfather)
2. Get the bot token (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
3. Create a channel or group
4. Add your bot to the channel/group
5. Get the chat ID (use [@userinfobot](https://t.me/userinfobot) or check bot updates)

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
