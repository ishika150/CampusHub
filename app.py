import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from postgrest import APIError
from supabase import Client, create_client


load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")


app = Flask(__name__)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def _response_data(response: Any) -> Any:
    return getattr(response, "data", response)


def _json_error(message: str, status_code: int):
    return jsonify({"error": message}), status_code


def _required_user_id() -> Optional[str]:
    user_id = request.headers.get("X-User-Id", "").strip()
    return user_id or None


def _database_error(error: Exception):
    app.logger.exception("Supabase request failed", exc_info=error)
    return _json_error("Database request failed", 500)


def _get_event(event_id: str) -> Optional[Dict[str, Any]]:
    response = (
        supabase.table("events")
        .select("*")
        .eq("id", event_id)
        .limit(1)
        .execute()
    )
    rows = _response_data(response) or []
    return rows[0] if rows else None


def _parse_pagination() -> Tuple[Optional[int], Optional[int], Optional[Tuple[Any, int]]]:
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        return None, None, _json_error("limit and offset must be integers", 400)

    if limit < 1 or limit > 100:
        return None, None, _json_error("limit must be between 1 and 100", 400)
    if offset < 0:
        return None, None, _json_error("offset must be zero or greater", 400)
    return limit, offset, None


def _is_duplicate_registration(error: Exception) -> bool:
    code = str(getattr(error, "code", ""))
    message = str(getattr(error, "message", error)).lower()
    return code in {"23505", "409"} or "duplicate" in message or "unique" in message


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (TypeError, ValueError, AttributeError):
        return False


@app.get("/")
def index():
    """Small smoke-test page retained from the starter app."""

    try:
        response = supabase.table("users").select("*").execute()
    except APIError as error:
        return f"<p>Error loading users: {error.message}</p>", 500

    users = _response_data(response) or []
    html = "<h1>Users</h1><ul>"
    for user in users:
        html += f'<li>{user.get("email", "")}</li>'
    html += "</ul>"
    return html


@app.get("/api/events")
def list_events():
    limit, offset, error = _parse_pagination()
    if error:
        return error

    try:
        response = (
            supabase.table("events")
            .select("*")
            .range(offset, offset + limit - 1)
            .execute()
        )
        return jsonify(_response_data(response) or [])
    except APIError as api_error:
        return _database_error(api_error)


@app.get("/api/events/<event_id>")
def event_details(event_id: str):
    if not _is_uuid(event_id):
        return _json_error("event_id must be a valid UUID", 400)

    try:
        event = _get_event(event_id)
        if event is None:
            return _json_error("Event not found", 404)
        return jsonify(event)
    except APIError as api_error:
        return _database_error(api_error)


@app.post("/api/registrations")
def create_registration():
    """Create a registration using the required X-User-Id header."""

    user_id = _required_user_id()
    if user_id is None:
        return _json_error("X-User-id header is required", 401)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("JSON body must include event_id", 400)
    if not isinstance(payload.get("event_id"), str):
        return _json_error("JSON body must include event_id", 400)

    event_id = payload["event_id"].strip()
    if not event_id:
        return _json_error("event_id must not be empty", 400)
    if not _is_uuid(event_id):
        return _json_error("event_id must be a valid UUID", 400)
    if not _is_uuid(user_id):
        return _json_error("X-User-id header must be a valid UUID", 400)

    try:
        event = _get_event(event_id)
        if event is None:
            return _json_error("Event not found", 404)
        if event.get("status") not in (None, "active"):
            return _json_error("Event is not accepting registrations", 409)

        existing = (
            supabase.table("registrations")
            .select("id")
            .eq("event_id", event_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if _response_data(existing):
            return _json_error("Registration already exists", 409)

        capacity = event.get("capacity")
        if capacity is not None:
            registrations = (
                supabase.table("registrations")
                .select("id", count="exact")
                .eq("event_id", event_id)
                .execute()
            )
            registration_count = getattr(registrations, "count", None)
            if registration_count is None:
                registration_count = len(_response_data(registrations) or [])
            if registration_count >= int(capacity):
                return _json_error("Event is full", 409)

        inserted = (
            supabase.table("registrations")
            .insert(
                {
                    "event_id": event_id,
                    "user_id": user_id,
                    "created_at": payload.get("created_at")
                    or datetime.now(timezone.utc).isoformat(),
                }
            )
            .select("*")
            .execute()
        )
        rows = _response_data(inserted) or []
        return jsonify(rows[0] if rows else {"event_id": event_id, "user_id": user_id}), 201
    except APIError as api_error:
        if _is_duplicate_registration(api_error):
            return _json_error("Registration already exists", 409)
        return _database_error(api_error)


@app.get("/api/me/registrations")
def my_registrations():
    """Return registrations for the user in the X-User-Id header."""

    user_id = _required_user_id()
    if user_id is None:
        return _json_error("X-User-id header is required", 401)
    if not _is_uuid(user_id):
        return _json_error("X-User-id header must be a valid UUID", 400)

    try:
        response = (
            supabase.table("registrations")
            .select("*,events(*)")
            .eq("user_id", user_id)
            .execute()
        )
        return jsonify(_response_data(response) or [])
    except APIError as api_error:
        return _database_error(api_error)


@app.get("/api/events/<event_id>/registrations")
def event_registrations(event_id: str):
    """Return an event's attendee list publicly."""

    if _required_user_id() is None:
        return _json_error("X-User-id header is required", 401)
    if not _is_uuid(event_id):
        return _json_error("event_id must be a valid UUID", 400)

    try:
        event = _get_event(event_id)
        if event is None:
            return _json_error("Event not found", 404)

        response = (
            supabase.table("registrations")
            .select("*")
            .eq("event_id", event_id)
            .execute()
        )
        return jsonify(_response_data(response) or [])
    except APIError as api_error:
        return _database_error(api_error)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
