import asyncio

# message_id -> role_name
active_events = {}
# event_key -> {"channel_id": int, "role_name": str}
event_resources = {}
# event_key -> asyncio.Lock (protege la creation locale contre la concurrence)
event_setup_locks: dict[str, asyncio.Lock] = {}

