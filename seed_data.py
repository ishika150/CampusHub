"""Idempotently seed the existing Supabase event tables with demo data."""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
from supabase import create_client


load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def rows(response: Any) -> List[Dict[str, Any]]:
    return getattr(response, "data", response) or []


def find_one(table: str, column: str, value: Any) -> Dict[str, Any] | None:
    result = (
        supabase.table(table)
        .select("*")
        .eq(column, value)
        .limit(1)
        .execute()
    )
    matches = rows(result)
    return matches[0] if matches else None


def ensure_event(event_data: Dict[str, Any]) -> Dict[str, Any]:
    existing = find_one("events", "title", event_data["title"])
    if existing:
        print(f"event exists: {existing['title']} ({existing['id']})")
        return existing

    created = rows(
        supabase.table("events").insert(event_data).select("*").execute()
    )
    event = created[0]
    print(f"event created: {event['title']} ({event['id']})")
    return event


def ensure_registration(event_id: str, user_id: str) -> Dict[str, Any]:
    existing = (
        supabase.table("registrations")
        .select("*")
        .eq("event_id", event_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    existing_rows = rows(existing)
    if existing_rows:
        registration = existing_rows[0]
        print(f"registration exists: {registration['id']}")
        return registration

    created = rows(
        supabase.table("registrations")
        .insert(
            {
                "event_id": event_id,
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .select("*")
        .execute()
    )
    registration = created[0]
    print(f"registration created: {registration['id']}")
    return registration


def main() -> None:
    users = rows(supabase.table("users").select("id,email,role").execute())
    if not users:
        raise RuntimeError(
            "The users table is empty. Create Supabase Auth users first, then rerun."
        )

    organizer = next((user for user in users if user.get("role") == "organizer"), users[0])

    print(f"using organizer: {organizer['id']}")
    print(f"seeding registrations for {len(users)} users")

    event_specs = [
        {
            "title": "API Design Workshop",
            "organiser_id": organizer["id"],
            "capacity": 50,
            "timestammp": "2026-11-15T10:00:00+05:30",
            "status": "active",
        },
        {
            "title": "Supabase Security Lab",
            "organiser_id": organizer["id"],
            "capacity": 25,
            "timestammp": "2026-11-22T14:00:00+05:30",
            "status": "active",
        },
    ]

    events = [ensure_event(event_data) for event_data in event_specs]
    for event in events:
        for user in users:
            ensure_registration(event["id"], user["id"])

    print("seed complete")
    for table in ("users", "events", "registrations"):
        count = len(rows(supabase.table(table).select("*").execute()))
        print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
