"""20 Questions (#78, #82): the box shows the questions and sends the taps; this asks a model for every turn.

Box -> bridge on `pet/q20`, never retained:
    {"game": 7, "op": "start"}
    {"game": 7, "op": "answer", "n": 4, "a": "yes" | "no" | "unsure"}      to a question
    {"game": 7, "op": "answer", "n": 9, "a": "right" | "wrong"}            to a guess
    {"game": 7, "op": "quit"}
Bridge -> box on `pet/q20/ask`, never retained:
    {"game": 7, "n": 5, "q": "Is it bigger than a loaf of bread?"}
    {"game": 7, "n": 9, "guess": "a toaster"}
    {"game": 7, "error": "The AI took longer than 40 s."}

`game` is a new number the box picks for every game (at random, so it does not repeat after a reboot). Turns run 1 to 20, and a question or a guess uses one each; turn 20 is
always a guess. "right" ends the game, and so does "wrong" on turn 20: the box knows both, so nothing is sent back.
One model call runs at a time; a game that starts while another box's turn is being worked on gets a "busy" error.

The model is PET_Q20_CMD, any command that reads the prompt on stdin and prints a reply holding {"q": ...} or
{"guess": ...}. The default is Claude Code on Haiku with the login already on this computer, so no API key: no tools
(the prompt only ever holds the box's taps and the model's own questions), no user settings, so the pet's own hooks
never turn a question into a waiting session, and no saved session.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

TOPIC_IN = "pet/q20"
TOPIC_ASK = "pet/q20/ask"
TURNS = 20
MAX_Q = 120        # characters the box shows for a question
MAX_GUESS = 60
MAX_GAMES = 4      # games the bridge remembers; the oldest goes first (a box that vanished mid-game)
DEFAULT_CMD = "claude -p --model haiku --tools '' --setting-sources '' --no-session-persistence"
DEFAULT_TIMEOUT = 40   # seconds for a turn, retry included; the box gives up after 50
MIN_RETRY = 5          # seconds a retry needs left, or the turn fails at once
QUESTION_ANSWERS = {"yes": "yes", "no": "no", "unsure": "not sure"}
GUESS_ANSWERS = {"right", "wrong"}
log = logging.getLogger("bridge")

PROMPT = """You are a small desk pet playing 20 Questions with the person at the desk. They are thinking of something:
an object, animal, food, place, person or idea. Ask short yes-or-no questions to narrow it down, and guess once you
are fairly sure. Every question and every guess uses one of 20 turns. Use plain words, at most 80 characters, and no
emoji.

Reply with one line of JSON and nothing else: either {"q": "your question"} or {"guess": "the thing"}, with just its
name in the guess, like "a toaster".

@HISTORY@

@TURN@"""


class ModelError(Exception):
    """The model could not be asked; the message goes to the box as it is, so keep it short."""


@dataclass
class Game:
    turns: list = field(default_factory=list)   # [kind ("q" or "guess"), text, answer or None]
    pending: int | None = None                  # the turn the model is working on


def build_prompt(turns: list, n: int) -> str:
    if turns:
        lines = ["Turns so far:"]
        for i, (kind, text, answer) in enumerate(turns, 1):
            said = QUESTION_ANSWERS.get(answer, answer)
            lines.append(f"{i}. Asked: {text} Answer: {said}" if kind == "q" else f"{i}. Guessed: {text}. Answer: {said}")
        history = "\n".join(lines)
    else:
        history = "No turns yet."
    turn = f"This is turn {n} of {TURNS}."
    if n >= TURNS:
        turn += " It is the last turn, so you must guess."
    return PROMPT.replace("@HISTORY@", history).replace("@TURN@", turn)


def clean(text: str, limit: int) -> str:
    """Plain ASCII on one line, which the box's font can draw, cut at a word to fit."""
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"),
                 ("…", "...")):
        text = text.replace(a, b)
    text = " ".join(text.encode("ascii", "ignore").decode().split())
    if len(text) > limit:
        text = text[:limit - 3].rsplit(" ", 1)[0] + "..."
    return text


def parse_reply(text: str, final: bool) -> dict | None:
    """{"q": ...} or {"guess": ...} from the model's reply, or None when it holds neither (or a question on turn 20)."""
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(obj.get("guess"), str) and clean(obj["guess"], MAX_GUESS):
            return {"guess": clean(obj["guess"], MAX_GUESS)}
        if isinstance(obj.get("q"), str) and clean(obj["q"], MAX_Q) and not final:
            return {"q": clean(obj["q"], MAX_Q)}
    return None


def command() -> list[str]:
    argv = shlex.split(os.environ.get("PET_Q20_CMD") or DEFAULT_CMD)
    if argv and not shutil.which(argv[0]):
        local = Path.home() / ".local" / "bin" / argv[0]   # where Claude Code installs itself, often not on a
        if local.exists():                                  # background process's PATH
            argv[0] = str(local)
    return argv


def run_command(argv: list[str], prompt: str, timeout: float) -> str:
    if not argv:
        raise ModelError("PET_Q20_CMD is empty on the bridge computer.")
    name = Path(argv[0]).name
    try:
        done = subprocess.run(argv, input=prompt, capture_output=True, text=True, errors="replace", timeout=timeout,
                              cwd=tempfile.gettempdir())
    except FileNotFoundError:
        raise ModelError(f"No {name} on the bridge computer: set PET_Q20_CMD.") from None
    except subprocess.TimeoutExpired:
        raise ModelError(f"The AI took longer than {timeout:.0f} s.") from None
    except OSError as exc:
        raise ModelError(f"Could not run {name} on the bridge computer ({exc.strerror}).") from None
    if done.returncode != 0:
        log.warning("q20: %s exited %d: %s", name, done.returncode, done.stderr.strip()[-300:])
        raise ModelError(f"The AI did not answer ({name} exited {done.returncode}).")
    return done.stdout


class Q20:
    def __init__(self, publish: Callable[[str, str], None], ask: Callable[[str, float], str] | None = None,
                 spawn: Callable[[Callable[[], None]], None] | None = None) -> None:
        self._publish = publish
        self._timeout = float(os.environ.get("PET_Q20_TIMEOUT") or DEFAULT_TIMEOUT)
        if ask is None:
            argv = command()
            ask = lambda prompt, timeout: run_command(argv, prompt, timeout)  # noqa: E731
        self._ask = ask   # (prompt, seconds) -> the model's reply
        self._spawn = spawn or (lambda f: threading.Thread(target=f, daemon=True).start())
        self._games: dict[int, Game] = {}
        self._calls = 0   # model calls running now: at most one, so a flood of starts cannot fork a flood of models
        self._lock = threading.Lock()

    def handle(self, payload) -> None:
        """One message from the box. Anything malformed, repeated (QoS 1 can deliver twice) or stale is dropped."""
        try:
            msg = json.loads(payload)
            gid, op = msg["game"], msg["op"]
        except (ValueError, TypeError, KeyError):
            log.warning("q20: unreadable message: %r", payload[:120])
            return
        if not isinstance(gid, int) or isinstance(gid, bool):
            return
        n, a = msg.get("n"), msg.get("a")
        if not isinstance(n, int) or isinstance(n, bool) or not isinstance(a, str):
            n, a = None, None
        busy = False
        with self._lock:
            game = self._games.get(gid)
            if op == "start" and game is None:
                while len(self._games) >= MAX_GAMES:
                    del self._games[next(iter(self._games))]
                game = self._games[gid] = Game()
                log.info("q20: game %d started", gid)
            elif op == "quit":
                self._games.pop(gid, None)
                log.info("q20: game %d closed on the box", gid)
                return
            elif op == "answer" and game is not None and game.pending is None and n == len(game.turns):
                kind, text, answer = game.turns[-1]
                if answer is not None or a not in (QUESTION_ANSWERS if kind == "q" else GUESS_ANSWERS):
                    return
                game.turns[-1][2] = a
                log.info("q20: game %d turn %d: %s", gid, len(game.turns), a)
                if a == "right" or len(game.turns) >= TURNS:
                    del self._games[gid]
                    log.info("q20: game %d over: %s", gid, "guessed it" if a == "right" else "out of turns")
                    return
            else:
                return
            if self._calls > 0:   # another game's turn is with the model
                del self._games[gid]
                busy = True
            else:
                self._calls += 1
                n = game.pending = len(game.turns) + 1
                prompt = build_prompt(game.turns, n)
        if busy:
            log.info("q20: game %d refused: the model is busy with another game", gid)
            self._publish(TOPIC_ASK, json.dumps({"game": gid, "error": "Busy with another game. Try again soon."}))
            return
        self._spawn(lambda: self._turn(gid, game, n, prompt))

    def _turn(self, gid: int, game: Game, n: int, prompt: str) -> None:
        """Ask the model for turn n (on its own thread: a call takes seconds) and send the box what it said."""
        final = n >= TURNS
        deadline = time.monotonic() + self._timeout
        try:
            reply = parse_reply(self._ask(prompt, self._timeout), final)
            if reply is None:   # one more try with a nudge, in the time left, before the box hears about it
                left = deadline - time.monotonic()
                if left < MIN_RETRY:
                    raise ModelError("The AI's reply did not make sense. Try again.")
                nudge = ("This is the last turn: reply with a guess, not a question." if final else
                         "Your last reply could not be read.")
                reply = parse_reply(self._ask(f"{prompt}\n\n{nudge} Reply with only the JSON line.", left), final)
            if reply is None:
                raise ModelError("The AI's reply did not make sense. Try again.")
        except ModelError as exc:
            out = {"game": gid, "error": clean(str(exc), MAX_Q)}
        except Exception:   # anything else still frees the game and tells the box
            log.exception("q20: game %d turn %d failed", gid, n)
            out = {"game": gid, "error": "Something went wrong on the bridge."}
        else:
            out = {"game": gid, "n": n, **reply}
        with self._lock:
            self._calls -= 1
            if self._games.get(gid) is not game or game.pending != n:
                return   # closed on the box while the model was thinking
            if "error" in out:
                del self._games[gid]
            else:
                game.turns.append(["q" if "q" in reply else "guess", next(iter(reply.values())), None])
                game.pending = None
        log.info("q20: game %d -> %s", gid, out)
        self._publish(TOPIC_ASK, json.dumps(out, separators=(",", ":")))


def attach(client) -> Q20:
    """Answer the box on pet/q20 from the bridge loop. The caller subscribes TOPIC_IN (so a reconnect does too)."""
    game = Q20(lambda topic, payload: client.publish(topic, payload, qos=1, retain=False))

    def on_message(c, userdata, msg) -> None:
        try:
            game.handle(msg.payload)
        except Exception:   # an exception here would stop paho's network thread, and the whole bridge with it
            log.exception("q20: message dropped: %r", msg.payload[:120])

    client.message_callback_add(TOPIC_IN, on_message)
    log.info("20 Questions: answering on %s with %s", TOPIC_IN, shlex.join(command()[:2]))
    return game
