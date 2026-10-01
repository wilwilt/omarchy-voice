"""Kitchen countdown timers, spoken.

The desk and the phone's hold-to-speak both land here as text. The phrases
are matched locally and written to the Kitchen Command list. Nothing in this
module calls Sonos, and a clock-time reminder is not a timer.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
MAX_MINUTES = 24 * 60
_ONES_WORD = r"one|two|three|four|five|six|seven|eight|nine"
_TENS_WORD = r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_UNDER_HUNDRED = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    rf"(?:{_TENS_WORD})(?:[ -](?:{_ONES_WORD}))?)"
)
# Hundreds before under-hundred so "one hundred" is not cut off at "one".
_NUMBER = (
    rf"(\d+|zero|(?:{_ONES_WORD}) hundred(?:(?: and)?[ -]{_UNDER_HUNDRED})?|{_UNDER_HUNDRED})"
)


def kitchen_base_url() -> str:
    return os.environ.get("KITCHEN_COMMAND_URL", "http://127.0.0.1:9190").rstrip("/")


def normalize(text: str) -> str:
    words = re.sub(r"[^a-z0-9\s-]", " ", (text or "").lower())
    words = words.replace("-", " ")
    words = re.sub(r"\s+", " ", words).strip()
    words = re.sub(r"^(?:hey |ok )?(?:buzz|oma|omar|ohma|alma) ", "", words)
    return words.strip()


def parse_number(token: str) -> int | None:
    token = token.replace("-", " ").strip().lower()
    if token.isdigit():
        return int(token)
    if token == "zero":
        return 0
    parts = token.split()
    if "hundred" in parts:
        idx = parts.index("hundred")
        left, right = parts[:idx], parts[idx + 1 :]
        if len(left) != 1 or left[0] not in ONES or ONES[left[0]] > 9:
            return None
        if right and right[0] == "and":
            right = right[1:]
        hundreds = ONES[left[0]] * 100
        if not right:
            return hundreds
        rest = parse_number(" ".join(right))
        if rest is None or rest < 1 or rest >= 100:
            return None
        return hundreds + rest
    if len(parts) == 1:
        if parts[0] in ONES:
            return ONES[parts[0]]
        if parts[0] in TENS:
            return TENS[parts[0]]
        return None
    if len(parts) == 2 and parts[0] in TENS and parts[1] in ONES and ONES[parts[1]] < 10:
        return TENS[parts[0]] + ONES[parts[1]]
    return None


def _start(name: str, token: str) -> dict | None:
    value = parse_number(token)
    if value is None:
        return None
    if value < 1:
        return {"action": "invalid", "error": "A timer needs at least one minute."}
    if value > MAX_MINUTES:
        return None
    return {"action": "start", "name": _name(name), "minutes": value}


def _name(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", (raw or "").strip())
    cleaned = re.sub(r"^(?:the|my)\s+", "", cleaned)
    if not cleaned or cleaned == "timer":
        return "Timer"
    return cleaned


def parse_utterance(text: str) -> dict | None:
    """Return an action dict, or None when this is not a kitchen timer phrase."""
    heard = normalize(text)
    if not heard:
        return None
    if heard in {"check timers", "check the timers", "check timer"}:
        return {"action": "check"}
    how_long = re.fullmatch(r"how long on (?:the )?(.+)", heard)
    if how_long:
        return {"action": "check", "name": _name(how_long.group(1))}
    if heard in {"cancel all timers", "cancel all the timers"}:
        return {"action": "cancel_all"}
    if heard in {"cancel the timer", "cancel timer"}:
        return {"action": "cancel", "name": "Timer"}
    cancel = re.fullmatch(r"cancel (?:the )?(.+?) timer", heard)
    if cancel:
        return {"action": "cancel", "name": _name(cancel.group(1))}
    bare = re.fullmatch(rf"(?:set |start )?(?:a |an )?timer for {_NUMBER} minutes?", heard)
    if bare:
        return _start("Timer", bare.group(1))
    named_after = re.fullmatch(
        rf"(?:set |start )?(?:a |an )?{_NUMBER} minutes? timer for (.+)", heard)
    if named_after:
        return _start(named_after.group(2), named_after.group(1))
    unnamed = re.fullmatch(rf"(?:set |start )?(?:a |an )?{_NUMBER} minutes? timer", heard)
    if unnamed:
        return _start("Timer", unnamed.group(1))
    named = re.fullmatch(rf"(?:set |start )?(?:a |an )?{_NUMBER} minutes? (.+?) timer", heard)
    if named:
        return _start(named.group(2), named.group(1))
    leading = re.fullmatch(rf"(.+?) timer (?:for )?{_NUMBER} minutes?", heard)
    if leading:
        return _start(leading.group(1), leading.group(2))
    return None


class KitchenClient:
    """HTTP client for the kitchen timer routes. Tests pass an opener."""

    def __init__(self, base_url: str | None = None, opener=None):
        self.base_url = (base_url or kitchen_base_url()).rstrip("/")
        self.opener = opener or urllib.request.urlopen

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Kitchen-Touch": "1",
            },
            method=method,
        )
        try:
            with self.opener(request, timeout=8) as response:
                body = response.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            try:
                parsed = json.loads(detail)
            except json.JSONDecodeError:
                parsed = {"ok": False, "error": detail[:300] or f"HTTP {exc.code}"}
            parsed["status"] = exc.code
            return parsed
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            return {"ok": False, "error": f"Kitchen Command is not reachable ({reason})."}

    def start(self, name: str, minutes: int) -> dict:
        return self._call("POST", "/api/kitchen/timers", {
            "name": name,
            "minutes": minutes,
            "source": "voice",
        })

    def list_timers(self) -> dict:
        return self._call("GET", "/api/kitchen/timers")

    def cancel(self, *, name: str | None = None, all_timers: bool = False) -> dict:
        payload: dict = {"all": True} if all_timers else {"name": name}
        return self._call("POST", "/api/kitchen/timers/cancel", payload)


def _remaining_phrase(timer: dict) -> str:
    name = timer.get("name") or "Timer"
    if timer.get("status") == "done" or int(timer.get("remainingSeconds") or 0) <= 0:
        return f"{name} is done"
    seconds = int(timer["remainingSeconds"])
    minutes, secs = divmod(seconds, 60)
    parts = []
    if minutes:
        parts.append(f"{minutes} minute" if minutes == 1 else f"{minutes} minutes")
    if secs or not parts:
        parts.append(f"{secs} second" if secs == 1 else f"{secs} seconds")
    return f"{name} has {' and '.join(parts)} left"


def _find(timers: list[dict], name: str) -> list[dict]:
    wanted = re.sub(r"\s+", " ", (name or "").strip()).lower()
    return [
        timer for timer in timers
        if re.sub(r"\s+", " ", str(timer.get("name") or "").strip()).lower() == wanted
    ]


def speak(parsed: dict, payload: dict) -> str:
    action = parsed["action"]
    if payload.get("ok") is False and payload.get("error"):
        if action == "check":
            return f"There isn't a {parsed.get('name', 'timer')} timer."
        return str(payload["error"])
    timers = payload.get("timers") or []
    if action == "start":
        timer = payload.get("timer") or {}
        name = timer.get("name") or parsed["name"]
        minutes = timer.get("minutes") or parsed["minutes"]
        unit = "minute" if minutes == 1 else "minutes"
        speaker = timer.get("speaker")
        line = f"{name} is set for {minutes} {unit}."
        if speaker == "busy":
            line += " The kitchen speaker is busy."
        elif speaker == "unreachable":
            line += " The kitchen speaker is unreachable."
        return line
    if action == "check" and parsed.get("name"):
        found = _find(timers, parsed["name"])
        if not found:
            return f"There isn't a {parsed['name']} timer."
        return " ".join(_remaining_phrase(timer) + "." for timer in found)
    if action == "check":
        if not timers:
            return "No timers."
        return " ".join(_remaining_phrase(timer) + "." for timer in timers)
    if action == "cancel":
        cleared = payload.get("names") or []
        if not cleared:
            return f"There isn't a {parsed['name']} timer."
        return f"Cancelled the {cleared[0]} timer."
    if action == "cancel_all":
        count = int(payload.get("cleared") or 0)
        if count == 0:
            return "No timers to cancel."
        unit = "timer" if count == 1 else "timers"
        return f"Cancelled {count} {unit}."
    return "Done."


def reply_to(text: str, client: KitchenClient | None = None) -> str | None:
    """Run a kitchen timer phrase. None means the words were not a timer."""
    parsed = parse_utterance(text)
    if parsed is None:
        return None
    if parsed["action"] == "invalid":
        return parsed["error"]
    api = client or KitchenClient()
    if parsed["action"] == "start":
        payload = api.start(parsed["name"], parsed["minutes"])
    elif parsed["action"] == "check":
        payload = api.list_timers()
    elif parsed["action"] == "cancel":
        payload = api.cancel(name=parsed["name"])
    else:
        payload = api.cancel(all_timers=True)
    if isinstance(payload, dict) and payload.get("status") == 404:
        payload = {**payload, "ok": False}
    return speak(parsed, payload if isinstance(payload, dict) else {})
