import json
import sys
import unittest

from petbrain.q20 import (TOPIC_ASK, TURNS, MAX_Q, ModelError, Q20, build_prompt, clean, parse_reply,
                          run_command)


class Box:
    """A Q20 with a scripted model, run on the calling thread, and what it sent to the box."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []
        self.sent = []
        self.q20 = Q20(lambda topic, payload: self.sent.append((topic, json.loads(payload))), ask=self.ask,
                       spawn=lambda f: f())

    def ask(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def send(self, **msg):
        self.q20.handle(json.dumps(msg))

    def last(self):
        topic, msg = self.sent[-1]
        self.assertTopic(topic)
        return msg

    @staticmethod
    def assertTopic(topic):
        assert topic == TOPIC_ASK, topic


class GameTests(unittest.TestCase):
    def test_a_game_goes_turn_by_turn(self):
        box = Box('{"q": "Is it alive?"}', 'Sure! {"q": "Does it fly?"}', '{"guess": "a cat"}')
        box.send(game=7, op="start")
        self.assertEqual(box.last(), {"game": 7, "n": 1, "q": "Is it alive?"})
        self.assertIn("No turns yet.", box.prompts[0])
        self.assertIn("This is turn 1 of 20.", box.prompts[0])
        box.send(game=7, op="answer", n=1, a="yes")
        self.assertEqual(box.last(), {"game": 7, "n": 2, "q": "Does it fly?"})
        self.assertIn("1. Asked: Is it alive? Answer: yes", box.prompts[1])
        box.send(game=7, op="answer", n=2, a="unsure")
        self.assertEqual(box.last(), {"game": 7, "n": 3, "guess": "a cat"})
        self.assertIn("2. Asked: Does it fly? Answer: not sure", box.prompts[2])
        box.send(game=7, op="answer", n=3, a="right")   # the box ends the game itself
        self.assertEqual(len(box.sent), 3)
        self.assertNotIn(7, box.q20._games)

    def test_a_wrong_guess_uses_a_turn_and_play_goes_on(self):
        box = Box('{"guess": "a dog"}', '{"q": "Is it big?"}')
        box.send(game=1, op="start")
        box.send(game=1, op="answer", n=1, a="wrong")
        self.assertEqual(box.last(), {"game": 1, "n": 2, "q": "Is it big?"})
        self.assertIn("1. Guessed: a dog. Answer: wrong", box.prompts[1])

    def test_repeats_stale_turns_and_wrong_kinds_are_dropped(self):
        box = Box('{"q": "Is it alive?"}', '{"q": "Is it red?"}')
        box.send(game=2, op="start")
        box.send(game=2, op="start")                      # a QoS 1 repeat
        box.send(game=2, op="answer", n=1, a="right")     # "right" answers a guess, not a question
        box.send(game=2, op="answer", n=5, a="yes")       # not the turn on screen
        box.send(game=9, op="answer", n=1, a="yes")       # no such game
        self.assertEqual(len(box.prompts), 1)
        box.send(game=2, op="answer", n=1, a="no")
        box.send(game=2, op="answer", n=1, a="no")        # a repeat of that answer
        self.assertEqual(len(box.prompts), 2)
        self.assertEqual(box.last()["n"], 2)

    def test_junk_is_ignored(self):
        box = Box()
        for payload in (b"not json", b"[]", b'{"game": "x", "op": "start"}', b'{"game": true, "op": "start"}',
                        b'{"game": 3, "op": "dance"}', b'{"op": "start"}'):
            box.q20.handle(payload)
        self.assertEqual(box.sent, [])

    def test_turn_20_must_be_a_guess_and_a_wrong_one_ends_the_game(self):
        box = Box('{"q": "Q1?"}')
        box.send(game=4, op="start")
        game = box.q20._games[4]
        game.turns = [["q", f"Q{i}?", "no"] for i in range(1, TURNS - 1)] + [["q", "Q19?", None]]
        box.replies = ['{"q": "One more?"}', '{"q": "Still asking?"}']
        box.send(game=4, op="answer", n=TURNS - 1, a="no")
        self.assertIn("It is the last turn, so you must guess.", box.prompts[-1])
        self.assertEqual(box.last(), {"game": 4, "error": "The AI's reply did not make sense. Try again."})
        self.assertNotIn(4, box.q20._games)

        box = Box('{"q": "Q1?"}')
        box.send(game=5, op="start")
        box.q20._games[5].turns = [["q", f"Q{i}?", "no"] for i in range(1, TURNS)] + [["guess", "a fox", None]]
        box.send(game=5, op="answer", n=TURNS, a="wrong")
        self.assertEqual(len(box.sent), 1)
        self.assertNotIn(5, box.q20._games)

    def test_an_unreadable_reply_gets_one_retry(self):
        box = Box("I think it is a fish!", '{"guess": "a fish"}')
        box.send(game=6, op="start")
        self.assertIn("could not be read", box.prompts[1])
        self.assertEqual(box.last(), {"game": 6, "n": 1, "guess": "a fish"})

    def test_a_model_failure_tells_the_box_and_forgets_the_game(self):
        box = Box(ModelError("The AI took longer than 40 s."))
        box.send(game=8, op="start")
        self.assertEqual(box.last(), {"game": 8, "error": "The AI took longer than 40 s."})
        self.assertNotIn(8, box.q20._games)

    def test_a_reply_after_quit_is_dropped(self):
        box = Box()
        sent = []
        box.q20._spawn = sent.append                     # hold the model call
        box.send(game=3, op="start")
        box.send(game=3, op="quit")
        box.replies = ['{"q": "Too late?"}']
        sent[0]()
        self.assertEqual(box.sent, [])


class TextTests(unittest.TestCase):
    def test_parse_reply(self):
        self.assertEqual(parse_reply('```json\n{"q": "Is it “round”?"}\n```', False),
                         {"q": 'Is it "round"?'})
        self.assertEqual(parse_reply('{"q": "x?"} {"guess": "a ball"}', True), {"guess": "a ball"})
        self.assertIsNone(parse_reply('{"q": "x?"}', True))
        self.assertIsNone(parse_reply('{"q": "   "}', False))
        self.assertIsNone(parse_reply("no json here", False))

    def test_clean(self):
        self.assertEqual(clean("  Is it a\n café \U0001f600 item… ", 80), "Is it a caf item...")
        long = clean("word " * 60, MAX_Q)
        self.assertLessEqual(len(long), MAX_Q)
        self.assertTrue(long.endswith("word..."))

    def test_prompt_lists_answers_in_words(self):
        p = build_prompt([["q", "Is it alive?", "unsure"], ["guess", "a cat", "wrong"]], 3)
        self.assertIn("1. Asked: Is it alive? Answer: not sure\n2. Guessed: a cat. Answer: wrong", p)
        self.assertIn("This is turn 3 of 20.", p)
        self.assertNotIn("@", p)


class CommandTests(unittest.TestCase):
    def test_runs_the_command_with_the_prompt_on_stdin(self):
        echo = [sys.executable, "-c", "import sys, json; print(json.dumps({'q': sys.stdin.read()[:5]}))"]
        self.assertEqual(json.loads(run_command(echo, "Hello there", 10)), {"q": "Hello"})

    def test_failures_become_short_messages(self):
        with self.assertRaisesRegex(ModelError, "took longer than"):
            run_command([sys.executable, "-c", "import time; time.sleep(5)"], "", 0.5)
        with self.assertRaisesRegex(ModelError, "exited 3"):
            run_command([sys.executable, "-c", "raise SystemExit(3)"], "", 10)
        with self.assertRaisesRegex(ModelError, "No no-such-model-cli on the bridge"):
            run_command(["no-such-model-cli"], "", 10)


if __name__ == "__main__":
    unittest.main()
