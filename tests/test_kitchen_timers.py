"""Spoken kitchen timer phrases hit the same list the glass polls.

The HTTP server here is a stub. It does not call Sonos and it does not start
Kitchen Command.
"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from omarchy_voice.kitchen_timers import KitchenClient, parse_utterance, reply_to


class Recorder(BaseHTTPRequestHandler):
    records = []
    timers = []

    def log_message(self, fmt, *args):
        return

    def _read(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Recorder.records.append(("GET", self.path, dict(self.headers), None))
        self._send(200, {"ok": True, "timers": list(Recorder.timers)})

    def do_POST(self):
        payload = self._read()
        Recorder.records.append(("POST", self.path, dict(self.headers), payload))
        if self.path == "/api/kitchen/timers":
            raw_name = str(payload.get("name") or "Timer")
            shown = "Timer" if raw_name.lower() == "timer" else " ".join(
                part[:1].upper() + part[1:] for part in raw_name.split())
            timer = {
                "id": "t1",
                "name": shown,
                "minutes": payload.get("minutes"),
                "status": "running",
                "source": payload.get("source"),
                "remainingSeconds": int(payload.get("minutes") or 0) * 60,
                "speaker": None,
            }
            Recorder.timers.append(timer)
            self._send(200, {"ok": True, "timer": timer, "timers": list(Recorder.timers)})
            return
        if self.path == "/api/kitchen/timers/cancel":
            if payload.get("all"):
                cleared = [timer["name"] for timer in Recorder.timers]
                Recorder.timers.clear()
                self._send(200, {"ok": True, "cleared": len(cleared), "names": cleared, "timers": []})
                return
            name = str(payload.get("name") or "").lower()
            kept = []
            gone = []
            for timer in Recorder.timers:
                if timer["name"].lower() == name and not gone:
                    gone.append(timer["name"])
                else:
                    kept.append(timer)
            Recorder.timers[:] = kept
            if not gone:
                self._send(404, {"ok": False, "error": f"no timer named {payload.get('name')}"})
                return
            self._send(200, {"ok": True, "cleared": 1, "names": gone, "timers": kept})
            return
        self._send(404, {"ok": False, "error": "no such route"})


class PhraseTests(unittest.TestCase):
    def setUp(self):
        Recorder.records = []
        Recorder.timers = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.client = KitchenClient(f"http://{host}:{port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_the_start_phrases_create_a_voice_timer(self):
        cases = [
            ("pasta timer ten minutes", "pasta", 10),
            ("set a ten minute pasta timer", "pasta", 10),
            ("timer for ten minutes", "Timer", 10),
            ("twenty minute rice timer", "rice", 20),
        ]
        for phrase, name, minutes in cases:
            Recorder.timers.clear()
            Recorder.records.clear()
            with self.subTest(phrase=phrase):
                parsed = parse_utterance(phrase)
                self.assertEqual(parsed["action"], "start")
                self.assertEqual(parsed["minutes"], minutes)
                said = reply_to(phrase, self.client)
                self.assertIn(str(minutes), said)
                method, path, headers, body = Recorder.records[-1]
                self.assertEqual((method, path), ("POST", "/api/kitchen/timers"))
                self.assertEqual(headers["X-Kitchen-Touch"], "1")
                self.assertEqual(body["source"], "voice")
                self.assertEqual(body["minutes"], minutes)
                self.assertEqual(body["name"].lower(), name.lower())

    def test_check_and_cancel_use_the_same_list(self):
        reply_to("pasta timer ten minutes", self.client)
        reply_to("set a twenty minute rice timer", self.client)
        how = reply_to("how long on the pasta", self.client)
        self.assertIn("Pasta has 10 minutes left.", how)
        each = reply_to("check timers", self.client)
        self.assertIn("Pasta has 10 minutes left.", each)
        self.assertIn("Rice has 20 minutes left.", each)
        one = reply_to("cancel the pasta timer", self.client)
        self.assertEqual(one, "Cancelled the Pasta timer.")
        rest = reply_to("cancel all timers", self.client)
        self.assertEqual(rest, "Cancelled 1 timer.")
        self.assertEqual(reply_to("check timers", self.client), "No timers.")

    def test_hyphenated_and_compound_lengths(self):
        cases = [
            ("Set a 10-minute pasta timer.", "pasta", 10),
            ("Set a ten-minute pasta timer", "pasta", 10),
            ("set a twenty five minute rice timer", "rice", 25),
            ("set a twenty-five minute rice timer", "rice", 25),
            ("rice timer twenty five minutes", "rice", 25),
        ]
        for phrase, name, minutes in cases:
            with self.subTest(phrase=phrase):
                parsed = parse_utterance(phrase)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed["action"], "start")
                self.assertEqual(parsed["name"], name)
                self.assertEqual(parsed["minutes"], minutes)

    def test_spoken_hundreds_and_timer_for_name(self):
        cases = [
            ("set a one hundred minute timer", "Timer", 100),
            ("set a one hundred and twenty minute timer", "Timer", 120),
            ("set a one hundred twenty minute timer", "Timer", 120),
            ("one hundred minute pasta timer", "pasta", 100),
            ("set a twenty minute timer for pasta", "pasta", 20),
            ("set a twenty minute timer for the pasta", "pasta", 20),
            ("set a twenty minute timer for my pasta", "pasta", 20),
            ("the pasta timer for ten minutes", "pasta", 10),
            ("set a 120 minute timer", "Timer", 120),
        ]
        for phrase, name, minutes in cases:
            with self.subTest(phrase=phrase):
                parsed = parse_utterance(phrase)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed["action"], "start")
                self.assertEqual(parsed["name"], name)
                self.assertEqual(parsed["minutes"], minutes)

    def test_timer_for_the_name_round_trips_with_cancel_and_check(self):
        started = parse_utterance("set a twenty minute timer for the pasta")
        cancel = parse_utterance("cancel the pasta timer")
        how = parse_utterance("how long on the pasta")
        self.assertEqual(started["name"], "pasta")
        self.assertEqual(cancel["name"], started["name"])
        self.assertEqual(how["name"], started["name"])
        reply_to("set a twenty minute timer for the pasta", self.client)
        self.assertIn("Pasta has 20 minutes left.", reply_to("how long on the pasta", self.client))
        self.assertEqual(reply_to("cancel the pasta timer", self.client),
                         "Cancelled the Pasta timer.")

    def test_zero_minute_timer_answers_directly(self):
        parsed = parse_utterance("set a zero minute timer")
        self.assertEqual(
            parsed,
            {"action": "invalid", "error": "A timer needs at least one minute."},
        )
        self.assertEqual(reply_to("set a zero minute timer", self.client),
                         "A timer needs at least one minute.")
        self.assertEqual(Recorder.records, [])

    def test_cancel_the_timer_clears_the_unnamed_one(self):
        self.assertEqual(parse_utterance("cancel the timer"), {"action": "cancel", "name": "Timer"})
        self.assertEqual(parse_utterance("cancel timer"), {"action": "cancel", "name": "Timer"})

    def test_a_clock_reminder_is_not_a_timer(self):
        self.assertIsNone(parse_utterance("remind me at four to call the vet"))
        self.assertIsNone(reply_to("remind me at four", self.client))
        self.assertEqual(Recorder.records, [])

    def test_a_name_that_is_not_title_case_can_be_checked_and_cancelled(self):
        Recorder.timers = [
            {
                "id": "bbq",
                "name": "BBQ",
                "minutes": 10,
                "status": "done",
                "source": "voice",
                "remainingSeconds": 0,
            },
            {
                "id": "mac",
                "name": "mac and cheese",
                "minutes": 12,
                "status": "running",
                "source": "touch",
                "remainingSeconds": 90,
            },
        ]
        heard = reply_to("how long on the bbq", self.client)
        self.assertEqual(heard, "BBQ is done.")
        cheese = reply_to("how long on the mac and cheese", self.client)
        self.assertIn("mac and cheese has", cheese)
        cancelled = reply_to("cancel the bbq timer", self.client)
        self.assertEqual(cancelled, "Cancelled the BBQ timer.")
        self.assertEqual([timer["name"] for timer in Recorder.timers], ["mac and cheese"])


class AnnounceTests(unittest.TestCase):
    def test_the_desktop_line_is_the_notification_body(self):
        from omarchy_voice.cli import cmd_announce

        feedback = mock.Mock()

        class Args:
            text = ["Pasta", "is", "done"]

        with mock.patch("omarchy_voice.feedback.Feedback", return_value=feedback):
            code = cmd_announce(Args(), object())
        self.assertEqual(code, 0)
        feedback.notify.assert_called_once_with("Kitchen", "Pasta is done", urgency="normal")
        feedback._speak_now.assert_called_once_with("Pasta is done")


if __name__ == "__main__":
    unittest.main()
