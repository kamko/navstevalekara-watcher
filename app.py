"""
Doctor Appointment Watcher - SaaS Application
Simple web app to monitor navstevalekara.sk for appointment slots
"""
import re
import uuid
import json
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from apscheduler.schedulers.background import BackgroundScheduler

# Database setup
import os
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
    doctor_url = Column(String(500), nullable=False)  # Added doctor URL
    doctor_code = Column(String(50), nullable=False)
    target_dates = Column(String, nullable=False)  # JSON string
    telegram_bot_token = Column(String(255), nullable=False)
    telegram_chat_id = Column(String(255), nullable=False)
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

# Scheduler
scheduler = BackgroundScheduler()


# Helper functions - Preserve exact logic from original doctor_notifier.py

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
    date_input: str = Form(...),
    telegram_bot_token: str = Form(...),
    telegram_chat_id: str = Form(...)
):
    """Create a new watcher."""
    db = SessionLocal()
    try:
        # Extract doctor code from URL
        try:
            doctor_code = extract_doctor_code(doctor_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Extract doctor name from URL (the part before -d)
        doctor_name_match = re.search(r'/([^/]+)-d\d+\.html$', doctor_url)
        if doctor_name_match:
            # Convert URL-friendly name to readable name
            doctor_name = doctor_name_match.group(1).replace('-', ' ').title()
        else:
            doctor_name = f"Doctor {doctor_code}"

        # Parse date input (supports both exact dates and week ranges)
        try:
            dates = parse_date_input(date_input)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Generate UUID
        watcher_uuid = str(uuid.uuid4())

        # Create watcher
        watcher = Watcher(
            uuid=watcher_uuid,
            doctor_name=doctor_name,
            doctor_url=doctor_url,
            doctor_code=doctor_code,
            target_dates=json.dumps(dates),
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
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


# Startup and shutdown events

@app.on_event("startup")
async def startup_event():
    """Load all active watchers and start scheduler."""
    print("Starting application...")

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
