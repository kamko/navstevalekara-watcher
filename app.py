"""
Doctor Appointment Watcher - SaaS Application
Simple web app to monitor navstevalekara.sk for appointment slots
"""
import os
import re
import uuid
import json
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from apscheduler.schedulers.background import BackgroundScheduler

# Load environment variables
load_dotenv()

# Mailjet configuration
MAILJET_API_KEY = os.getenv('MAILJET_API_KEY')
MAILJET_SECRET_KEY = os.getenv('MAILJET_SECRET_KEY')
MAILJET_SENDER_EMAIL = os.getenv('MAILJET_SENDER_EMAIL')
MAILJET_SENDER_NAME = os.getenv('MAILJET_SENDER_NAME', 'Doctor Appointment Watcher')


def validate_mailjet_config() -> bool:
    """Check if Mailjet is properly configured."""
    return bool(MAILJET_API_KEY and MAILJET_SECRET_KEY and MAILJET_SENDER_EMAIL)


# Log Mailjet status
if validate_mailjet_config():
    print("✓ Mailjet configured - Email notifications enabled")
else:
    print("⚠ Mailjet not configured - Only Telegram notifications available")

# Database setup
os.makedirs('data', exist_ok=True)
DATABASE_URL = "sqlite:///./data/watchers.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Database Models
class Watcher(Base):
    __tablename__ = "watchers"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), unique=True, index=True, nullable=False)
    doctor_name = Column(String(255), nullable=False)
    doctor_url = Column(String(500), nullable=False)
    doctor_code = Column(String(50), nullable=False)
    target_dates = Column(String, nullable=False)  # JSON string

    # Notification type selection
    notification_type = Column(String(20), nullable=False, default='telegram')  # 'telegram' or 'email'

    # Telegram fields - now nullable for email-only watchers
    telegram_bot_token = Column(String(255), nullable=True)
    telegram_chat_id = Column(String(255), nullable=True)

    # Email field
    email = Column(String(255), nullable=True)

    is_active = Column(Boolean, default=True)
    last_check_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NotifiedSlot(Base):
    __tablename__ = "notified_slots"

    id = Column(Integer, primary_key=True, index=True)
    watcher_id = Column(Integer, nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    time = Column(String(5), nullable=False)   # HH:MM
    notified_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('watcher_id', 'date', 'time', name='_watcher_slot_uc'),
    )


# Create tables
Base.metadata.create_all(bind=engine)


# FastAPI app
app = FastAPI(title="Doctor Appointment Watcher")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Scheduler
scheduler = BackgroundScheduler()


# Helper functions

def calculate_week_offsets_for_dates(target_dates: List[str]) -> List[int]:
    """Calculate which week offsets to check based on target dates."""
    from datetime import datetime, timedelta

    today = datetime.now().date()
    week_offsets = set()

    for date_str in target_dates:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        days_diff = (target_date - today).days

        # Week offset calculation:
        # Week 0 = current week (Mon-Sun containing today)
        # Week 1 = next week, etc.
        week_offset = days_diff // 7

        # If target date is in the past, skip it
        if days_diff < 0:
            continue

        week_offsets.add(week_offset)

    return sorted(week_offsets)


def check_appointments(doctor_code: str, doctor_url: str, week_offset: int) -> Optional[str]:
    """Make POST request to check for available appointments for a specific week."""
    url = "https://www.navstevalekara.sk/page/modules/doctors/order.php"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0',
        'Accept': 'text/html, */*; q=0.01',
        'Accept-Language': 'sk,en-US;q=0.7,en;q=0.3',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin': 'https://www.navstevalekara.sk',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
        'Referer': doctor_url,
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin'
    }

    data = {
        't': 'w',
        'dc': doctor_code,
        'w': str(week_offset)
    }

    try:
        response = requests.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch appointments for week {week_offset}: {e}")
        return None


def parse_available_slots(html_content: str) -> List[dict]:
    """Parse HTML to extract ONLY available appointment slots (with onclick)."""
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    available_slots = []

    # Find all day columns
    day_columns = soup.find_all('div', class_='day-col')

    for column in day_columns:
        # ONLY look for slots with onclick attribute (available slots)
        # Reserved slots are <span class="reserved">, available are <a> with onclick
        available_links = column.find_all('a', href='javascript:;')

        for link in available_links:
            onclick_attr = link.get('onclick', '')

            # Only process if onclick contains get_order function
            if 'get_order' in onclick_attr:
                # Extract date and time from onclick attribute
                # Format: get_order('2025-12-30', 2, '09:00', 20, false)
                onclick_match = re.search(r"get_order\('([^']+)',\s*\d+,\s*'([^']+)'", onclick_attr)

                if onclick_match:
                    full_date = onclick_match.group(1)  # e.g., '2025-12-30'
                    time_slot = onclick_match.group(2)   # e.g., '09:00'

                    slot_info = {
                        'date': full_date,
                        'time': time_slot,
                        'datetime': f"{full_date} {time_slot}"
                    }
                    available_slots.append(slot_info)

    return available_slots


def send_telegram_notification(bot_token: str, chat_id: str, doctor_name: str, doctor_url: str, slots: List[dict]) -> bool:
    """Send notification via Telegram for multiple slots in one message."""
    if not slots:
        return True

    # Build message with clickable doctor name and all slots
    doctor_link = f"[{doctor_name}]({doctor_url})"

    if len(slots) == 1:
        message = f"{doctor_link}\n\nTermín: {slots[0]['date']} {slots[0]['time']} - OPEN"
    else:
        message = f"{doctor_link}\n\nNájdených {len(slots)} termínov:\n\n"
        for slot in sorted(slots, key=lambda x: x['datetime']):
            message += f"• {slot['date']} {slot['time']}\n"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Notification sent with {len(slots)} slot(s)")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to send Telegram notification: {e}")
        return False


def send_email_notification(email: str, doctor_name: str, doctor_url: str, slots: List[dict]) -> bool:
    """Send notification via Email using Mailjet."""
    if not slots:
        return True

    if not MAILJET_API_KEY or not MAILJET_SECRET_KEY or not MAILJET_SENDER_EMAIL:
        print("ERROR: Mailjet not configured")
        return False

    try:
        from mailjet_rest import Client

        mailjet = Client(auth=(MAILJET_API_KEY, MAILJET_SECRET_KEY), version='v3.1')

        subject = f"{len(slots)} {'nový termín' if len(slots) == 1 else 'nových termínov'} u {doctor_name}"

        html_content = build_email_html(doctor_name, doctor_url, slots)
        text_content = build_email_text(doctor_name, doctor_url, slots)

        data = {
            'Messages': [{
                "From": {"Email": MAILJET_SENDER_EMAIL, "Name": MAILJET_SENDER_NAME},
                "To": [{"Email": email}],
                "Subject": subject,
                "TextPart": text_content,
                "HTMLPart": html_content,
            }]
        }

        result = mailjet.send.create(data=data)

        if result.status_code == 200:
            print(f"Email sent to {email} with {len(slots)} slot(s)")
            return True
        else:
            print(f"Email failed: {result.status_code}")
            return False

    except Exception as e:
        print(f"Email error: {e}")
        return False


def build_email_html(doctor_name: str, doctor_url: str, slots: List[dict]) -> str:
    """Build rich HTML email template."""
    sorted_slots = sorted(slots, key=lambda x: x['datetime'])

    template = templates.get_template("email_notification.html")
    return template.render(
        doctor_name=doctor_name,
        doctor_url=doctor_url,
        slots=sorted_slots
    )


def build_email_text(doctor_name: str, doctor_url: str, slots: List[dict]) -> str:
    """Build plain text email fallback."""
    sorted_slots = sorted(slots, key=lambda x: x['datetime'])

    text = f"{doctor_name}\n\n"
    text += f"{'Nájdený 1 voľný termín:' if len(slots) == 1 else f'Nájdených {len(slots)} voľných termínov:'}\n\n"

    for slot in sorted_slots:
        text += f"• {slot['date']} {slot['time']}\n"

    text += f"\n\nObjednať sa: {doctor_url}\n"
    return text


# Background job function
def check_watcher_job(watcher_id: int):
    """Background job to check appointments for a watcher."""
    db = SessionLocal()
    try:
        watcher = db.query(Watcher).filter(Watcher.id == watcher_id).first()

        if not watcher or not watcher.is_active:
            return

        print(f"Checking watcher {watcher.id} - {watcher.doctor_name}")

        # Parse target dates
        target_dates = json.loads(watcher.target_dates)

        # Calculate week offsets
        week_offsets = calculate_week_offsets_for_dates(target_dates)

        if not week_offsets:
            print(f"Target dates {target_dates} are in the past. Nothing to check.")
            return

        # Use the stored doctor URL
        doctor_url = watcher.doctor_url

        # Check all weeks
        all_slots = []
        for week in week_offsets:
            html_content = check_appointments(watcher.doctor_code, doctor_url, week)

            if html_content:
                slots = parse_available_slots(html_content)
                # Filter to only target dates
                filtered_slots = [s for s in slots if s['date'] in target_dates]
                all_slots.extend(filtered_slots)

        # Check for new slots (not in notified_slots table)
        new_slots = []
        for slot in all_slots:
            exists = db.query(NotifiedSlot).filter(
                NotifiedSlot.watcher_id == watcher.id,
                NotifiedSlot.date == slot['date'],
                NotifiedSlot.time == slot['time']
            ).first()

            if not exists:
                new_slots.append(slot)

        # Send notification for new slots
        if new_slots:
            print(f"Found {len(new_slots)} new slot(s)")

            # Route based on notification type
            if watcher.notification_type == 'email':
                success = send_email_notification(
                    watcher.email,
                    watcher.doctor_name,
                    doctor_url,
                    new_slots
                )
            else:  # telegram (default)
                success = send_telegram_notification(
                    watcher.telegram_bot_token,
                    watcher.telegram_chat_id,
                    watcher.doctor_name,
                    doctor_url,
                    new_slots
                )

            if success:
                # Record notified slots
                for slot in new_slots:
                    notified = NotifiedSlot(
                        watcher_id=watcher.id,
                        date=slot['date'],
                        time=slot['time']
                    )
                    db.add(notified)
                db.commit()
        else:
            print(f"No new slots found")

        # Auto-delete unavailable slots
        currently_available_set = set()
        for slot in all_slots:
            currently_available_set.add(f"{slot['date']}_{slot['time']}")

        # Get all notified slots for this watcher
        all_notified_slots = db.query(NotifiedSlot).filter(
            NotifiedSlot.watcher_id == watcher.id
        ).all()

        # Delete slots that are no longer available
        deleted_count = 0
        for notified_slot in all_notified_slots:
            slot_key = f"{notified_slot.date}_{notified_slot.time}"
            if slot_key not in currently_available_set:
                # Slot is no longer available - delete it
                db.delete(notified_slot)
                deleted_count += 1

        if deleted_count > 0:
            db.commit()
            print(f"Auto-deleted {deleted_count} unavailable slot(s)")

        # Update last check time
        watcher.last_check_at = datetime.utcnow()
        db.commit()

    except Exception as e:
        print(f"Error checking watcher {watcher_id}: {e}")
        db.rollback()
    finally:
        db.close()


# Routes

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Landing page with create watcher form."""
    return templates.TemplateResponse("index.html", {"request": request})


def extract_doctor_code(url: str) -> str:
    """Extract doctor code from navstevalekara.sk URL."""
    # URL format: ...d15313.html or ...d84.html
    import re
    match = re.search(r'-d(\d+)\.html$', url)
    if not match:
        raise ValueError("Could not extract doctor code from URL. URL must end with '-dXXX.html'")
    return match.group(1)


def extract_doctor_name_from_page(doctor_url: str) -> Optional[str]:
    """Extract doctor name with diacritics from the doctor's page HTML."""
    try:
        response = requests.get(doctor_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for doctor name in h1 tag
        h1 = soup.find('h1')
        if h1:
            return h1.get_text(strip=True)

        # Fallback: look for title tag
        title = soup.find('title')
        if title:
            title_text = title.get_text(strip=True)
            # Remove common suffixes from title
            title_text = re.sub(r'\s*-\s*navstevalekara\.sk.*$', '', title_text, flags=re.IGNORECASE)
            return title_text.strip()

        return None
    except Exception as e:
        print(f"Failed to extract doctor name from page: {e}")
        return None


def parse_date_input(date_input: str) -> List[str]:
    """Parse date input - supports exact dates or week ranges."""
    from datetime import datetime, timedelta

    date_input = date_input.strip()
    dates = []

    # Check if it's a week range (e.g., "0-3")
    if re.match(r'^\d+-\d+$', date_input):
        start_week, end_week = map(int, date_input.split('-'))
        today = datetime.now().date()

        # Calculate start of current week (Monday)
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday)

        # Generate all dates in the week range
        for week in range(start_week, end_week + 1):
            for day in range(7):  # All days of the week
                date = week_start + timedelta(weeks=week, days=day)
                dates.append(date.strftime('%Y-%m-%d'))
    else:
        # Exact dates (one per line)
        dates = [d.strip() for d in date_input.split('\n') if d.strip()]

        # Validate dates
        for date_str in dates:
            try:
                datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD or week range (e.g., 0-3)")

    return dates


@app.post("/create")
async def create_watcher(
    request: Request,
    doctor_url: str = Form(...),
    target_dates: List[str] = Form(...),
    notification_type: str = Form(...),
    telegram_bot_token: Optional[str] = Form(None),
    telegram_chat_id: Optional[str] = Form(None),
    email: Optional[str] = Form(None)
):
    """Create a new watcher."""
    db = SessionLocal()
    try:
        # Extract doctor code from URL
        try:
            doctor_code = extract_doctor_code(doctor_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Extract doctor name from page HTML (with diacritics)
        doctor_name = extract_doctor_name_from_page(doctor_url)
        if not doctor_name:
            # Fallback: extract from URL (without diacritics)
            doctor_name_match = re.search(r'/([^/]+)-d\d+\.html$', doctor_url)
            if doctor_name_match:
                doctor_name = doctor_name_match.group(1).replace('-', ' ').title()
            else:
                doctor_name = f"Doctor {doctor_code}"

        # Validate and deduplicate dates
        validated_dates = []
        for date_str in target_dates:
            if not date_str:  # Skip empty values
                continue
            try:
                datetime.strptime(date_str, '%Y-%m-%d')
                if date_str not in validated_dates:
                    validated_dates.append(date_str)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Neplatný dátum: {date_str}")

        if not validated_dates:
            raise HTTPException(status_code=400, detail="Musíte vybrať aspoň jeden dátum")

        dates = validated_dates

        # Validate notification type
        if notification_type not in ['telegram', 'email']:
            raise HTTPException(status_code=400, detail="Neplatný typ notifikácie")

        if notification_type == 'telegram':
            if not telegram_bot_token or not telegram_chat_id:
                raise HTTPException(status_code=400, detail="Pre Telegram sú povinné Bot Token a Chat ID")
        elif notification_type == 'email':
            if not email:
                raise HTTPException(status_code=400, detail="Pre Email je povinná emailová adresa")
            # Email validation
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email):
                raise HTTPException(status_code=400, detail="Neplatná emailová adresa")

        # Generate UUID
        watcher_uuid = str(uuid.uuid4())

        # Create watcher
        watcher = Watcher(
            uuid=watcher_uuid,
            doctor_name=doctor_name,
            doctor_url=doctor_url,
            doctor_code=doctor_code,
            target_dates=json.dumps(dates),
            notification_type=notification_type,
            telegram_bot_token=telegram_bot_token if notification_type == 'telegram' else None,
            telegram_chat_id=telegram_chat_id if notification_type == 'telegram' else None,
            email=email if notification_type == 'email' else None,
            is_active=True
        )

        db.add(watcher)
        db.commit()
        db.refresh(watcher)

        # Schedule background job (every 5 minutes)
        scheduler.add_job(
            check_watcher_job,
            'interval',
            minutes=5,
            id=f"watcher_{watcher.id}",
            args=[watcher.id],
            replace_existing=True
        )

        print(f"Created watcher {watcher.id} - {watcher.doctor_name}")

        # Run check immediately as a one-time job
        from datetime import datetime as dt
        scheduler.add_job(
            check_watcher_job,
            'date',
            run_date=dt.now(),
            args=[watcher.id],
            id=f"watcher_{watcher.id}_immediate"
        )
        print(f"Triggered immediate check for watcher {watcher.id}")

        return RedirectResponse(url=f"/w/{watcher_uuid}", status_code=303)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/w/{watcher_uuid}", response_class=HTMLResponse)
async def view_watcher(request: Request, watcher_uuid: str):
    """View watcher details."""
    db = SessionLocal()
    try:
        watcher = db.query(Watcher).filter(Watcher.uuid == watcher_uuid).first()

        if not watcher:
            raise HTTPException(status_code=404, detail="Watcher not found")

        # Get notified slots
        notified_slots = db.query(NotifiedSlot).filter(
            NotifiedSlot.watcher_id == watcher.id
        ).order_by(NotifiedSlot.notified_at.desc()).limit(20).all()

        # Parse target dates
        target_dates = json.loads(watcher.target_dates)

        return templates.TemplateResponse("watcher.html", {
            "request": request,
            "watcher": watcher,
            "target_dates": target_dates,
            "notified_slots": notified_slots
        })

    finally:
        db.close()


@app.post("/w/{watcher_uuid}/toggle")
async def toggle_watcher(watcher_uuid: str):
    """Toggle watcher active status."""
    db = SessionLocal()
    try:
        watcher = db.query(Watcher).filter(Watcher.uuid == watcher_uuid).first()

        if not watcher:
            raise HTTPException(status_code=404, detail="Watcher not found")

        watcher.is_active = not watcher.is_active
        db.commit()

        # Update scheduler
        job_id = f"watcher_{watcher.id}"
        if watcher.is_active:
            # Add job
            scheduler.add_job(
                check_watcher_job,
                'interval',
                minutes=5,
                id=job_id,
                args=[watcher.id],
                replace_existing=True
            )
            print(f"Activated watcher {watcher.id}")
        else:
            # Remove job
            try:
                scheduler.remove_job(job_id)
                print(f"Deactivated watcher {watcher.id}")
            except:
                pass

        return RedirectResponse(url=f"/w/{watcher_uuid}", status_code=303)

    finally:
        db.close()


@app.post("/w/{watcher_uuid}/delete")
async def delete_watcher(watcher_uuid: str):
    """Delete a watcher."""
    db = SessionLocal()
    try:
        watcher = db.query(Watcher).filter(Watcher.uuid == watcher_uuid).first()

        if not watcher:
            raise HTTPException(status_code=404, detail="Watcher not found")

        # Remove scheduler job
        job_id = f"watcher_{watcher.id}"
        try:
            scheduler.remove_job(job_id)
        except:
            pass

        # Delete watcher and related notified slots
        db.query(NotifiedSlot).filter(NotifiedSlot.watcher_id == watcher.id).delete()
        db.delete(watcher)
        db.commit()

        print(f"Deleted watcher {watcher.id}")

        return RedirectResponse(url="/", status_code=303)

    finally:
        db.close()


@app.get("/w/{watcher_uuid}/slots")
async def get_notified_slots(watcher_uuid: str):
    """Get all notified slots for a watcher."""
    db = SessionLocal()
    try:
        watcher = db.query(Watcher).filter(Watcher.uuid == watcher_uuid).first()

        if not watcher:
            raise HTTPException(status_code=404, detail="Watcher not found")

        # Get all notified slots
        notified_slots = db.query(NotifiedSlot).filter(
            NotifiedSlot.watcher_id == watcher.id
        ).order_by(NotifiedSlot.notified_at.desc()).all()

        result_slots = []
        for notified_slot in notified_slots:
            result_slots.append({
                "id": notified_slot.id,
                "date": notified_slot.date,
                "time": notified_slot.time,
                "notified_at": notified_slot.notified_at.isoformat()
            })

        return {"slots": result_slots}

    finally:
        db.close()


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    """Secret admin panel to view and manage all watchers."""
    db = SessionLocal()
    try:
        # Get all watchers
        watchers = db.query(Watcher).order_by(Watcher.created_at.desc()).all()

        # Calculate stats
        total_watchers = len(watchers)
        active_watchers = sum(1 for w in watchers if w.is_active)
        total_slots = db.query(NotifiedSlot).count()

        # Enrich watcher data
        enriched_watchers = []
        for watcher in watchers:
            target_dates = json.loads(watcher.target_dates)
            slots_count = db.query(NotifiedSlot).filter(
                NotifiedSlot.watcher_id == watcher.id
            ).count()

            enriched_watcher = watcher
            enriched_watcher.target_dates_count = len(target_dates)
            enriched_watcher.slots_count = slots_count
            enriched_watchers.append(enriched_watcher)

        return templates.TemplateResponse("admin.html", {
            "request": request,
            "watchers": enriched_watchers,
            "total_watchers": total_watchers,
            "active_watchers": active_watchers,
            "total_slots": total_slots
        })

    finally:
        db.close()


# Startup and shutdown events

@app.on_event("startup")
async def startup_event():
    """Load all active watchers and start scheduler."""
    print("Starting application...")

    # Migrate existing watchers to have notification_type
    db = SessionLocal()
    try:
        existing = db.query(Watcher).filter(
            Watcher.notification_type == None
        ).all()

        for watcher in existing:
            watcher.notification_type = 'telegram'

        if existing:
            db.commit()
            print(f"Migrated {len(existing)} existing watcher(s) to telegram notification type")
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        db.close()

    # Start scheduler
    scheduler.start()
    print("Scheduler started")

    # Load all active watchers
    db = SessionLocal()
    try:
        active_watchers = db.query(Watcher).filter(Watcher.is_active == True).all()

        for watcher in active_watchers:
            scheduler.add_job(
                check_watcher_job,
                'interval',
                minutes=5,
                id=f"watcher_{watcher.id}",
                args=[watcher.id],
                replace_existing=True
            )
            print(f"Loaded watcher {watcher.id} - {watcher.doctor_name}")

        print(f"Loaded {len(active_watchers)} active watcher(s)")

    finally:
        db.close()


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown scheduler."""
    scheduler.shutdown()
    print("Scheduler stopped")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
