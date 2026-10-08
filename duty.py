"""Watch one yield note from open to outcome, and say what happened.

A note is one option the owner sold on Derive, fully collateralised. The
yield-notes skill opens it and files this duty with the fill's own numbers.
Three moments matter and the owner should never have to ask about any of them:

    the day before   where the asset is, and which way the note is leaning
    settlement       what they kept, or what they agreed to buy or sell
    after that       the asset itself, if they asked to actually own it

A duty has no network of its own, so the settlement price comes through the
options rail (`bevo.read("/options/settlement")`). The underlying is the token
pinned in TOKEN_ID when the duty was filed: the heads-up prices exactly that
pin with `/token-stats`, and the delivery buy trades the same pin.

Derive settles options in **cash**. An in-the-money put reduces USDC; it does
not hand over ETH. So "you now own 2.04 ETH" is only true when DELIVER_ASSET is
on and the spot buy actually executed. Until then the duty says the owner is
holding the loss in cash, because that is what has happened.

Settings: INSTRUMENT, PRODUCT, UNDERLYING, TOKEN_ID, STRIKE, SIZE, PREMIUM_USD,
COLLATERAL_USD, DELIVER_ASSET, HEADS_UP_HOURS.
"""

import bevo
import datetime
import json
import math
import os
import re
import subprocess
import time

PARAMS = json.loads(os.environ.get("PARAMS", "{}"))

INSTRUMENT = str(PARAMS.get("INSTRUMENT") or "").strip().upper()
PRODUCT = str(PARAMS.get("PRODUCT") or "").strip()
UNDERLYING = str(PARAMS.get("UNDERLYING") or "").strip().upper()
STRIKE = float(PARAMS.get("STRIKE") or 0)
SIZE = float(PARAMS.get("SIZE") or 0)
PREMIUM_USD = float(PARAMS.get("PREMIUM_USD") or 0)
COLLATERAL_USD = float(PARAMS.get("COLLATERAL_USD") or 0)
DELIVER_ASSET = bool(PARAMS.get("DELIVER_ASSET") or False)
TOKEN_ID = str(PARAMS.get("TOKEN_ID") or "").strip()
HEADS_UP_HOURS = float(PARAMS.get("HEADS_UP_HOURS") or 24)

NAME = os.environ.get("BEVO_SERVICE_NAME") or "your note"

#: Settlement is published shortly after the 08:00 UTC expiry, not at the
#: stroke of it. Below this the duty waits quietly rather than reporting a
#: missing price as a problem.
SETTLE_GRACE_SECONDS = 20 * 60

#: Past this with still no price, say so out loud: a settlement that never
#: lands is an incident, not a delay.
SETTLE_ALARM_SECONDS = 6 * 3600

PROBLEM = ""


def say(text):
    """`bevo.log()` on one line: an echoed error must not forge a log line."""
    bevo.log(" ".join(str(text).split()))


def fmt(number):
    return ("%.8f" % float(number)).rstrip("0").rstrip(".")


def usd(number):
    return "$%s" % format(round(float(number), 2), ",.2f")


def price(number):
    """Prices are read aloud, so trim them to something a person says."""
    n = float(number)
    return "$%s" % format(round(n, 2 if n < 100 else 0), ",.2f" if n < 100 else ",.0f")


def answer_of(text):
    """The JSON `acp` printed, or None. None is NOT a refusal."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def parse_instrument(name):
    """Pull expiry and option type back out of ETH-20261030-2450-P.

    Derive writes a decimal strike with an underscore (ADA-...-0_25-P), so the
    strike is parsed but never trusted over the STRIKE setting.
    """
    m = re.match(r"^([A-Z0-9]+)-(\d{8})-([0-9_]+)-([PC])$", name)
    if not m:
        return None
    _, yyyymmdd, _strike, kind = m.groups()
    try:
        # Pinned to UTC: read in a DST zone this is an hour out, enough to
        # call settlement before it has happened.
        moment = datetime.datetime.strptime(yyyymmdd + " 08:00", "%Y%m%d %H:%M")
        expiry = int(moment.replace(tzinfo=datetime.timezone.utc).timestamp())
    except ValueError:
        return None
    return {"expiry": expiry, "kind": kind}


DETAIL = parse_instrument(INSTRUMENT) if INSTRUMENT else None

if not DETAIL:
    PROBLEM = "INSTRUMENT %r is not a Derive option name like ETH-20261030-2450-P" % INSTRUMENT
elif PRODUCT not in ("cash_secured_put", "covered_call"):
    PROBLEM = "PRODUCT must be cash_secured_put or covered_call, not %r" % PRODUCT
elif DETAIL["kind"] == "P" and PRODUCT != "cash_secured_put":
    PROBLEM = "%s is a put but PRODUCT says %s" % (INSTRUMENT, PRODUCT)
elif DETAIL["kind"] == "C" and PRODUCT != "covered_call":
    PROBLEM = "%s is a call but PRODUCT says %s" % (INSTRUMENT, PRODUCT)
elif not INSTRUMENT.startswith(UNDERLYING + "-"):
    PROBLEM = "%s is not an option on UNDERLYING %s" % (INSTRUMENT, UNDERLYING)
elif SIZE <= 0 or STRIKE <= 0:
    PROBLEM = "SIZE and STRIKE must both be above zero"

def parse_pin(pin):
    """`native:<chainId>` or `<address>:<chainId>` → (token-out, chain id). None otherwise.

    A tokenized stock (`stock:<TICKER>:0`) is no option's underlying, so it is
    not accepted here.
    """
    m = re.match(r"^(native|0x[0-9a-fA-F]{40}):([0-9]+)$", pin)
    if not m:
        return None
    token, chain = m.groups()
    return ("eth" if token == "native" else token), int(chain)


PIN = parse_pin(TOKEN_ID) if TOKEN_ID else None

if not PROBLEM and not PIN:
    PROBLEM = ("TOKEN_ID %r is not a pinned token like native:8453 or <address>:<chainId>"
               % TOKEN_ID)

EXPIRY = DETAIL["expiry"] if DETAIL else 0
IS_PUT = PRODUCT == "cash_secured_put"


def spot_now():
    """What the pinned underlying is worth right now, or None for no price this run.

    For the heads-up only; settlement never reads this, Derive settles on its
    own index. An empty answer is a gap, not a zero.
    """
    rows = (bevo.read("/token-stats", {"tokens": TOKEN_ID}) or {}).get("tokens") or []
    if not rows or rows[0].get("priceUsd") is None:
        return None
    return float(rows[0]["priceUsd"])


def settlement_price():
    """The settlement Derive printed for this expiry.

    None means not published yet. A BevoError means the rail cannot answer at
    all — a different thing, and the owner is told about it rather than being
    given a guess.
    """
    body = bevo.read("/options/settlement", {"underlying": UNDERLYING, "expiry": EXPIRY}) or {}
    value = body.get("price")
    if value in (None, ""):
        return None
    settled = float(value)
    return settled if settled > 0 else None


def heads_up():
    """Once, the day before: where the asset is and which way this is leaning."""
    try:
        spot = spot_now()
    except bevo.BevoError as exc:
        say("could not read the price for the heads-up: %s" % exc)
        return
    if spot is None:
        say("no price for %s this run; the heads-up waits for the next tick" % TOKEN_ID)
        return

    hours = max(0, (EXPIRY - time.time()) / 3600.0)
    if IS_PUT:
        leaning = ("looks set to pay out in full" if spot >= STRIKE else
                   "is under your price, so you may end up buying %s at %s"
                   % (UNDERLYING, price(STRIKE)))
    else:
        leaning = ("looks set to pay out in full, and you keep your %s" % UNDERLYING
                   if spot <= STRIKE else
                   "is above your price, so your %s may be sold at %s"
                   % (UNDERLYING, price(STRIKE)))
    bevo.notify("%s: %s is at %s. Your note %s. It settles in about %d hours."
                % (NAME, UNDERLYING, price(spot), leaning, round(hours)))
    bevo.state["heads_up_sent"] = True


def outcome_sentences(settled):
    """What actually happened, in the owner's terms. Returns (headline, assigned).

    The assigned-put headline says "agreed to buy", not "have bought". Derive
    settles in cash, so until the delivery buy executes the owner owns no
    asset — and settle() is the only thing that knows whether it did.
    """
    if IS_PUT:
        if settled >= STRIKE:
            kept = COLLATERAL_USD or (STRIKE * SIZE)
            return ("%s settled at %s, above your %s. You kept your %s and the %s premium."
                    % (UNDERLYING, price(settled), price(STRIKE), usd(kept), usd(PREMIUM_USD)), False)
        shortfall = (STRIKE - settled) * SIZE
        net = PREMIUM_USD - shortfall
        return ("%s settled at %s, under your %s. Your note was assigned: you agreed to buy "
                "%s %s at %s, which is %s more than they are worth today. After the %s premium "
                "you are %s %s."
                % (UNDERLYING, price(settled), price(STRIKE), fmt(SIZE), UNDERLYING,
                   price(STRIKE), usd(shortfall), usd(PREMIUM_USD),
                   "down" if net < 0 else "up", usd(abs(net))), True)
    if settled <= STRIKE:
        return ("%s settled at %s, under your %s. You kept your %s and the %s premium."
                % (UNDERLYING, price(settled), price(STRIKE), UNDERLYING, usd(PREMIUM_USD)), False)
    given_up = (settled - STRIKE) * SIZE
    return ("%s settled at %s, above your %s. Your %s %s was sold at %s. You gave up %s of "
            "further upside, and kept the %s premium."
            % (UNDERLYING, price(settled), price(STRIKE), fmt(SIZE), UNDERLYING,
               price(STRIKE), usd(given_up), usd(PREMIUM_USD)), True)


def deliver(settled):
    """Buy the asset the owner has just agreed to buy. One key, never retried.

    It buys the pinned token (TOKEN_ID) on the pin's own chain, never a bare
    ticker. `acp trade` spends USDC, so SIZE of the asset is bought as SIZE x
    the settlement price in USDC, rounded up to the cent: roughly SIZE units,
    since the market has moved on since 08:00 UTC.

    Returns "done", "pending", "refused" or "unknown". An unparseable answer
    is "unknown", never "refused": the request may have landed, and only
    `bevo.exec_status(key)` can say.
    """
    key = "note:%s:deliver" % bevo.SERVICE_ID
    token_out, chain = PIN
    spend = math.ceil(SIZE * settled * 100) / 100.0
    done = subprocess.run(
        ["acp", "trade", "--token-in", "usdc", "--chain-in", str(chain),
         "--amount-in", "%.2f" % spend, "--token-out", token_out,
         "--chain-out", str(chain), "--idempotency-key", key],
        capture_output=True, text=True, timeout=180, check=False,
    )
    answer = answer_of(done.stdout)
    if answer is None:
        state = (bevo.exec_status(key) or {}).get("state")
        say("delivery buy outcome UNKNOWN (exec_status: %s) - not retried" % state)
        return "unknown"
    if answer.get("executed"):
        return "done"
    if answer.get("asked") or answer.get("ok"):
        return "pending"
    if answer.get("unrecognized"):
        say("delivery buy: unrecognised answer %r - treated as unknown" % answer.get("status"))
        return "unknown"
    say("delivery buy refused: %s" % (answer.get("error") or answer.get("status")))
    return "refused"


def settle():
    """Read the settlement, say what happened, deliver if asked, and finish."""
    late = time.time() - EXPIRY
    try:
        settled = settlement_price()
    except bevo.BevoError as exc:
        say("could not read settlement: %s" % exc)
        if late > SETTLE_ALARM_SECONDS and not bevo.state.get("settle_alarm"):
            bevo.state["settle_alarm"] = True
            bevo.notify("%s: your note has expired and I cannot read what it settled at, so I "
                        "will not guess. Your money is not at risk from this - it is my read "
                        "that is broken. I will keep trying." % NAME)
        bevo.fail("settlement unreadable: %s" % exc)
        return

    if settled is None:
        if late > SETTLE_ALARM_SECONDS and not bevo.state.get("settle_alarm"):
            bevo.state["settle_alarm"] = True
            bevo.notify("%s: your note expired %d hours ago and Derive has not published a "
                        "settlement price yet. Nothing is lost and I am still watching."
                        % (NAME, round(late / 3600)))
        else:
            say("settlement not published yet (%d min past expiry)" % round(late / 60))
        return

    headline, assigned = outcome_sentences(settled)
    bevo.state["settled_price"] = settled

    tail = ""
    if assigned and IS_PUT:
        if DELIVER_ASSET:
            result = deliver(settled)
            if result == "done":
                tail = " I have bought the %s for you, so you now own about %s %s." % (
                    UNDERLYING, fmt(SIZE), UNDERLYING)
            elif result == "pending":
                tail = (" Buying the %s is waiting on your approval; until it goes through "
                        "you are holding the loss in cash, not the %s." % (UNDERLYING, UNDERLYING))
            elif result == "refused":
                tail = " I could not buy the %s, so you are holding the loss in cash." % UNDERLYING
            else:
                tail = (" I am not certain whether the %s purchase went through; check before "
                        "buying again." % UNDERLYING)
        else:
            tail = (" Derive settles in cash, so you are holding the loss in cash, not the %s."
                    % UNDERLYING)
    if IS_PUT:
        tail += " What is left of your collateral is free cash in your Derive account."
    else:
        tail += " What is left of your %s collateral is free again in your Derive account." % UNDERLYING

    bevo.done("%s: %s%s" % (NAME, headline, tail))


def fire():
    left = EXPIRY - time.time()

    if left > HEADS_UP_HOURS * 3600:
        say("%s: %.1f days to expiry" % (INSTRUMENT, left / 86400.0))
        return

    if left > 0:
        if not bevo.state.get("heads_up_sent"):
            heads_up()
        else:
            say("%s: %.1f hours to expiry, heads-up already sent" % (INSTRUMENT, left / 3600.0))
        return

    if -left < SETTLE_GRACE_SECONDS:
        say("%s expired %d min ago; waiting for the settlement price" % (INSTRUMENT, round(-left / 60)))
        return

    settle()


if PROBLEM:
    bevo.notify("%s does nothing until its settings are fixed: %s." % (NAME, PROBLEM))

for tick in bevo.ticks():
    if PROBLEM:
        bevo.fail("settings: %s" % PROBLEM)
        continue
    fire()
