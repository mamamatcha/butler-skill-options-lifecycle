"""duty.py run against scripted reads, clocks and acp answers.

    python3 -m unittest discover -s tests -v
"""

import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_bevo import CALL, EXPIRY, PUT, BevoError, answer, hours, run  # noqa: E402

SOURCE = (Path(__file__).resolve().parent.parent / "duty.py").read_text()
ASKS = re.compile(r"\?|\bsay the word\b|\bwant me to\b|\bshall i\b|\bshould i\b|\breply\b", re.I)

SETTLED_AT = EXPIRY + hours(1)
PRICE = {"/token-price": {"priceUsd": "2705.5"}}


def settled(value):
    return {"/options/settlement": {"price": value}}


class HeadsUp(unittest.TestCase):
    def test_nothing_while_far_from_expiry(self):
        fake = run(PUT, [EXPIRY - hours(72)], PRICE)
        self.assertEqual(fake.notes, [])
        self.assertEqual(fake.read_calls, [])

    def test_once_inside_the_window_with_the_ticker_read_by_q(self):
        fake = run(PUT, [EXPIRY - hours(20), EXPIRY - hours(19)], PRICE)
        self.assertEqual(len(fake.notes), 1)
        self.assertIn("looks set to pay out in full", fake.notes[0])
        self.assertEqual(fake.read_calls[0], ("/token-price", {"q": "ETH"}))
        self.assertTrue(fake.state["heads_up_sent"])

    def test_put_under_the_strike_warns_of_buying(self):
        fake = run(PUT, [EXPIRY - hours(5)], {"/token-price": {"priceUsd": "2300"}})
        self.assertIn("you may end up buying ETH at $2,450", fake.notes[0])

    def test_call_over_the_strike_warns_of_selling(self):
        fake = run(CALL, [EXPIRY - hours(5)], {"/token-price": {"priceUsd": "3100"}})
        self.assertIn("your ETH may be sold at $3,000", fake.notes[0])

    def test_an_unreadable_price_skips_quietly_and_retries(self):
        fake = run(PUT, [EXPIRY - hours(5)], {"/token-price": BevoError("down")})
        self.assertEqual(fake.notes, [])
        self.assertNotIn("heads_up_sent", fake.state)


class Settlement(unittest.TestCase):
    def test_waits_through_the_grace_period_without_reading(self):
        fake = run(PUT, [EXPIRY + 600], settled("2600"))
        self.assertEqual(fake.read_calls, [])
        self.assertEqual(fake.dones, [])

    def test_asks_for_exactly_this_expiry(self):
        fake = run(PUT, [SETTLED_AT], settled("2600"))
        self.assertEqual(fake.read_calls[0], ("/options/settlement", {"underlying": "ETH", "expiry": EXPIRY}))

    def test_put_out_of_the_money_keeps_everything_and_finishes(self):
        fake = run(PUT, [SETTLED_AT, SETTLED_AT + 900], settled("2600"))
        self.assertEqual(len(fake.dones), 1)
        self.assertIn("You kept your $4,998.00 and the $55.17 premium", fake.dones[0])
        self.assertEqual(fake.acp_calls, [])

    def test_put_assigned_without_delivery_holds_the_loss_in_cash(self):
        fake = run(PUT, [SETTLED_AT], settled("2200"))
        text = fake.dones[0]
        self.assertIn("you agreed to buy 2.04 ETH at $2,450", text)
        self.assertIn("$510.00 more than they are worth", text)  # (2450-2200) x 2.04
        self.assertIn("you are down $454.83", text)               # 510 - 55.17
        self.assertIn("holding the loss in cash", text)
        self.assertNotIn("you now own", text)
        self.assertEqual(fake.acp_calls, [])

    def test_call_out_of_the_money_keeps_the_asset(self):
        fake = run(CALL, [SETTLED_AT], settled("2900"))
        self.assertIn("You kept your ETH and the $40.25 premium", fake.dones[0])

    def test_call_assigned_reports_the_upside_given_up(self):
        fake = run(CALL, [SETTLED_AT], settled("3200"))
        self.assertIn("Your 1.5 ETH was sold at $3,000", fake.dones[0])
        self.assertIn("gave up $300.00", fake.dones[0])

    def test_unpublished_waits_then_says_so_once_after_six_hours(self):
        late = [SETTLED_AT, EXPIRY + hours(7), EXPIRY + hours(8)]
        fake = run(PUT, late, settled(None))
        self.assertEqual(len(fake.notes), 1)
        self.assertIn("has not published a settlement price", fake.notes[0])
        self.assertEqual(fake.dones, [])

    def test_a_broken_read_never_becomes_an_outcome(self):
        late = [SETTLED_AT, EXPIRY + hours(7), EXPIRY + hours(8)]
        fake = run(PUT, late, {"/options/settlement": BevoError("503")})
        self.assertEqual(fake.dones, [])
        self.assertEqual(len(fake.fails), 3)
        self.assertEqual(len(fake.notes), 1)
        self.assertIn("will not guess", fake.notes[0])

    def test_a_zero_price_is_not_a_settlement(self):
        fake = run(PUT, [SETTLED_AT], settled("0"))
        self.assertEqual(fake.dones, [])


class Delivery(unittest.TestCase):
    DELIVER = dict(PUT, DELIVER_ASSET=True, CHAIN_ID=8453)

    def test_buys_size_times_settlement_with_one_literal_key(self):
        fake = run(self.DELIVER, [SETTLED_AT], settled("2200"), acp=[answer(executed=True)])
        self.assertEqual(fake.acp_calls, [[
            "acp", "trade", "--token-in", "usdc", "--chain-in", "8453",
            "--amount-in", "4488.00", "--token-out", "eth", "--chain-out", "8453",
            "--idempotency-key", "note:svc-1:deliver",
        ]])
        self.assertIn("you now own about 2.04 ETH", fake.dones[0])

    def test_an_approval_card_is_pending_not_owned(self):
        fake = run(self.DELIVER, [SETTLED_AT], settled("2200"), acp=[answer(asked=True, status="manual")])
        self.assertIn("waiting on your approval", fake.dones[0])
        self.assertNotIn("you now own", fake.dones[0])

    def test_an_unreadable_answer_is_unknown_and_checked_not_retried(self):
        fake = run(self.DELIVER, [SETTLED_AT], settled("2200"), acp=["Error: socket hang up"],
                   statuses={"note:svc-1:deliver": {"state": "in_flight"}})
        self.assertEqual(len(fake.acp_calls), 1)
        self.assertIn("not certain whether the ETH purchase went through", fake.dones[0])
        self.assertTrue(any("exec_status: in_flight" in line for line in fake.logs))

    def test_a_refusal_holds_the_loss_in_cash(self):
        fake = run(self.DELIVER, [SETTLED_AT], settled("2200"), acp=[answer(error="wallet_short")])
        self.assertIn("could not buy the ETH", fake.dones[0])

    def test_never_buys_after_a_put_that_kept_its_collateral(self):
        fake = run(self.DELIVER, [SETTLED_AT], settled("2600"), acp=[answer(executed=True)])
        self.assertEqual(fake.acp_calls, [])


class Settings(unittest.TestCase):
    def test_a_put_filed_as_a_call_does_nothing(self):
        fake = run(dict(PUT, PRODUCT="covered_call"), [SETTLED_AT], settled("2200"))
        self.assertIn("does nothing until its settings are fixed", fake.notes[0])
        self.assertEqual(fake.read_calls, [])
        self.assertEqual(len(fake.fails), 1)

    def test_the_underlying_must_match_the_instrument(self):
        fake = run(dict(PUT, UNDERLYING="BTC"), [SETTLED_AT], settled("2200"))
        self.assertIn("not an option on UNDERLYING BTC", fake.notes[0])

    def test_a_bad_instrument_name_does_nothing(self):
        fake = run(dict(PUT, INSTRUMENT="ETH-OCT30-2450-P"), [SETTLED_AT], settled("2200"))
        self.assertIn("is not a Derive option name", fake.notes[0])


class Boundary(unittest.TestCase):
    def test_every_acp_call_carries_a_literal_idempotency_key(self):
        calls = [n for n in ast.walk(ast.parse(SOURCE))
                 if isinstance(n, ast.Call) and ast.unparse(n.func) == "subprocess.run"]
        self.assertTrue(calls)
        for call in calls:
            argv = call.args[0]
            literals = [e.value for e in argv.elts if isinstance(e, ast.Constant)]
            self.assertEqual(literals[0], "acp")
            self.assertIn("--idempotency-key", literals)

    def test_no_note_asks_the_owner_anything(self):
        worlds = [
            run(PUT, [EXPIRY - hours(5)], {"/token-price": {"priceUsd": "2300"}}),
            run(PUT, [SETTLED_AT], settled("2200")),
            run(PUT, [SETTLED_AT], settled("2600")),
            run(CALL, [SETTLED_AT], settled("3200")),
            run(PUT, [SETTLED_AT, EXPIRY + hours(7)], settled(None)),
            run(PUT, [SETTLED_AT, EXPIRY + hours(7)], {"/options/settlement": BevoError("x")}),
            run(dict(PUT, DELIVER_ASSET=True), [SETTLED_AT], settled("2200"), acp=[answer(asked=True)]),
        ]
        for fake in worlds:
            for text in fake.notes + [d for d in fake.dones if d]:
                self.assertIsNone(ASKS.search(text), text)


if __name__ == "__main__":
    unittest.main()
