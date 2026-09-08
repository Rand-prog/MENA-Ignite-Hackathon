"""Pre-serialized JSON for the polled read endpoints.

FastAPI runs whatever a route returns through `jsonable_encoder` before
the response class ever sees it — a recursive walk of the whole payload
testing every value against pydantic-model / dataclass / enum / date /
Decimal / etc. On `/dashboard/trips` with 12 live trips that was 23% of
the endpoint's CPU (profiled), and all of it is redundant here:
serializers.py already emits nothing but str / int / float / bool / None /
list / dict — the ISO timestamps and enum values are converted there, by
hand, because the demo contract pins those exact string shapes anyway.

Returning a `Response` instance is FastAPI's own documented way to say
"this is already the body" and skip that pass. Used only on the endpoints
that are actually polled on a timer (the dashboard's 3-second queue poll,
the app's trip poll, the conductor's api-log poll); everywhere else the
normal return-a-dict path stays, because there the encoder costs nothing
worth this indirection.

`json.dumps` here is deliberately strict: a datetime or an Enum that ever
reaches it raises TypeError rather than being silently coerced, which is
the failure mode you want when the contract is "serializers.py already
did this."
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Response


def json_ok(payload: Any) -> Response:
    return Response(
        content=json.dumps(payload, separators=(",", ":")),
        media_type="application/json",
    )
