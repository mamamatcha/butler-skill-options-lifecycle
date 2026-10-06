"""Watch one yield note from open to outcome, and say what happened.

A note is one option the owner sold, fully collateralised. Three moments
matter and the owner should never have to ask about any of them:

    the day before   where the asset is, and which way the note is leaning
    settlement       what they kept, or what they have just bought
    after that       the asset itself, if they asked to actually own it

Everything here reads Derive's **public** API. The note's own details arrive
as settings when the duty is filed, and Derive publishes settlement prices
openly, so the outcome needs no key, no session and no options rail. That is
deliberate: the lifecycle works before the rail exists, and keeps working if
a key is ever rotated out from under it.

Derive settles options in **cash**. An in-the-money put reduces USDC; it does
not hand over ETH. So "you now own 2.04 ETH at $2,450" is only true when
DELIVER_ASSET is on and the spot buy actually fills. Until then the duty says
the owner is down money, because that is what has happened.

Settings: INSTRUMENT, PRODUCT, UNDERLYING, STRIKE, SIZE, PREMIUM_USD,
COLLATERAL_USD, DELIVER_ASSET, CHAIN_ID, HEADS_UP_HOURS.
"""

import bevo
import datetime
import json
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
CHAIN_ID = int(PARAMS.get("CHAIN_ID") or 8453)
HEADS_UP_HOURS = float(PARAMS.get("HEADS_UP_HOURS") or 24)

NAME = os.environ.get("BEVO_SERVICE_NAME") or "your note"

#: Where the rail publishes the settlement price Derive printed for an
#: expiry. A duty has no network of its own - everything goes through
#: bevo.read - so this is the one thing the options rail must expose before
#: the outcome half of this duty can work. The heads-up half works today.
SETTLEMENT_PATH = "/options/settlement"

#: Settlement is published shortly after the 08:00 UTC expiry, not at the
#: stroke of it. Below this the duty waits quietly rather than reporting a
#: missing price as a problem.
SETTLE_GRACE_SECONDS = 20 * 60

#: Past this with still no published price, say so out loud: a settlement
#: that never lands is an incident, not a delay.
SETTLE_ALARM_SECONDS = 6 * 3600

#: exec_status states after which the delivery buy is no longer in flight.
SETTLED_STATES = {
    "executed", "failed", "refused", "rejected", "expired", "cancelled",
    "canceled", "declined", "replay", "unknown",
}

PROBLEM = ""


# --- small helpers -------------------------------------------------------------------------


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


# --- the note ------------------------------------------------------------------------------


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
        # Pinned to UTC explicitly. Derive expiries are 08:00 UTC, and reading
        # them in the container's local zone would be an hour out under DST -
        # enough to call settlement before it has happened.
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
elif SIZE <= 0 or STRIKE <= 0:
    PROBLEM = "SIZE and STRIKE must both be above zero"

EXPIRY = DETAIL["expiry"] if DETAIL else 0
IS_PUT = PRODUCT == "cash_secured_put"


def spot_now():
    """What the underlying is worth right now. Existing endpoint."""
    body = bevo.read("/token-price", {"symbol": UNDERLYING}) or {}
    for field in ("price", "usd", "value"):
        if body.get(field):
            return float(body[field])
    raise RuntimeError("no price for %s in /token-price" % UNDERLYING)


def settlement_price():
    """The settlement Derive printed for this expiry.

    None means not published yet. RuntimeError means the rail cannot answer
    at all - a different thing, and the owner is told about it rather than
    being given a guess.
    """
    body = bevo.read(SETTLEMENT_PATH, {"underlying": UNDERLYING, "expiry": EXPIRY}) or {}
    value = body.get("price")
    if value in (None, "", 0, "0"):
        return None
    return float(value)


# --- the three moments ---------------------------------------------------------------------


def heads_up():
    """Once, the day before: where the asset is and which way this is leaning."""
    try:
        spot = spot_now()
    except Exception as exc:  # noqa: BLE001 - a missed heads-up must not kill the duty
        say("could not read the price for the heads-up: %s" % exc)
        return

    hours = max(0, (EXPIRY - time.time()) / 3600.0)
    if IS_PUT:
        safe = spot >= STRIKE
        leaning = ("looks set to pay out in full" if safe else
                   "is under your price, so you may end up buying %s at %s"
                   % (UNDERLYING, price(STRIKE)))
        line = ("%s: %s is at %s. Your note %s. It settles in about %d hours."
                % (NAME, UNDERLYING, price(spot), leaning, round(hours)))
    else:
        safe = spot <= STRIKE
        leaning = ("looks set to pay out in full, and you keep your %s" % UNDERLYING
                   if safe else
                   "is above your price, so your %s may be sold at %s"
                   % (UNDERLYING, price(STRIKE)))
        line = ("%s: %s is at %s. Your note %s. It settles in about %d hours."
                % (NAME, UNDERLYING, price(spot), leaning, round(hours)))

    bevo.notify(line[:500])
    bevo.state["heads_up_sent"] = True


def outcome_sentences(settled):
    """What actually happened, in the owner's terms. Returns (headline, assigned)."""
    if IS_PUT:
        if settled >= STRIKE:
            kept = COLLATERAL_USD or (STRIKE * SIZE)
            return ("%s settled at %s, above your %s. You kept %s and the %s."
                    % (UNDERLYING, price(settled), price(STRIKE),
                       usd(kept), usd(PREMIUM_USD)), False)
        shortfall = (STRIKE - settled) * SIZE
        net = PREMIUM_USD - shortfall
        return ("%s settled at %s, under your %s. You have bought %s %s at %s. "
                "That is %s below the market, and after the %s premium you are %s %s."
                % (UNDERLYING, price(settled), price(STRIKE), fmt(SIZE), UNDERLYING,
                   price(STRIKE), usd(shortfall), usd(PREMIUM_USD),
                   "down" if net < 0 else "up", usd(abs(net))), True)
    if settled <= STRIKE:
        return ("%s settled at %s, under your %s. You kept your %s and the %s."
                % (UNDERLYING, price(settled), price(STRIKE), UNDERLYING,
                   usd(PREMIUM_USD)), False)
    given_up = (settled - STRIKE) * SIZE
    return ("%s settled at %s, above your %s. Your %s %s sold at %s. You gave up %s "
            "of further upside, and kept the %s."
            % (UNDERLYING, price(settled), price(STRIKE), fmt(SIZE), UNDERLYING,
               price(STRIKE), usd(given_up), usd(PREMIUM_USD)), True)


def deliver():
    """Buy the asset the owner has just agreed to buy. One key, never retried."""
    key = "note:%s:deliver" % bevo.SERVICE_ID
    done = subprocess.run(
        ["acp", "trade", "buy", UNDERLYING,
         "--amount", fmt(SIZE), "--chain", str(CHAIN_ID),
         "--idempotency-key", key],
        capture_output=True, text=True, timeout=180, check=False,
    )
    answer = answer_of(done.stdout)
    if answer is None:
        state = (bevo.exec_status(key) or {}).get("state")
        say("delivery buy outcome UNKNOWN (exec_status: %s) - not retried" % state)
        return "unknown"
    status = str(answer.get("status") or "").lower()
    if answer.get("executed") or status == "executed":
        return "done"
    if answer.get("asked") or status in ("asked", "awaiting_approval", "pending_approval"):
        return "pending"
    if answer.get("ok") or status in ("accepted", "executing", "pending", "replay"):
        return "pending"
    return "refused"


def settle():
    """Read the settlement, say what happened, and deliver if asked."""
    try:
        settled = settlement_price()
    except Exception as exc:  # noqa: BLE001 - the rail is allowed a bad minute
        say("could not read settlement: %s" % exc)
        if time.time() - EXPIRY > SETTLE_ALARM_SECONDS and not bevo.state.get("settle_alarm"):
            bevo.state["settle_alarm"] = True
            bevo.notify(("%s: your note has expired and I cannot read what it settled "
                         "at, so I will not guess. Your money is not at risk from this "
                         "- it is my read that is broken. Check Derive and I will keep "
                         "trying." % NAME)[:500])
        return

    late = time.time() - EXPIRY
    if settled is None:
        if late > SETTLE_ALARM_SECONDS and not bevo.state.get("settle_alarm"):
            bevo.state["settle_alarm"] = True
            bevo.notify(("%s: your note expired %d hours ago and %s has not published a "
                         "settlement price yet. Nothing is lost - I am still watching - "
                         "but I wanted you to hear it from me."
                         % (NAME, round(late / 3600), "Derive"))[:500])
        else:
            say("settlement not published yet (%d min past expiry)" % round(late / 60))
        return

    headline, assigned = outcome_sentences(settled)
    bevo.state["settled_price"] = settled

    tail = ""
    if assigned and IS_PUT:
        if DELIVER_ASSET:
            result = deliver()
            if result == "done":
                tail = " I have bought the %s for you." % UNDERLYING
            elif result == "pending":
                tail = " The purchase is waiting on your approval."
            elif result == "refused":
                tail = (" I could not buy the %s - that part needs you."
                        % UNDERLYING)
            else:
                tail = (" I am not certain whether the %s purchase went through; "
                        "check before buying again." % UNDERLYING)
        else:
            tail = (" Derive settles in cash, so you are holding the loss, not the %s. "
                    "Say the word and I will buy it." % UNDERLYING)
    elif not assigned:
        tail = " Want me to set up another one?"

    bevo.notify((NAME + ": " + headline + tail)[:500])
    bevo.state["done"] = True


# --- each fire -----------------------------------------------------------------------------


def fire():
    if bevo.state.get("done"):
        return

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

    if -left < SETTLE_GRACE_SECONDS and not bevo.state.get("settled_price"):
        say("%s expired %d min ago; waiting for the settlement price"
            % (INSTRUMENT, round(-left / 60)))
        return

    settle()


if PROBLEM:
    bevo.notify("%s does nothing until its settings are fixed: %s." % (NAME, PROBLEM))

for tick in bevo.ticks():
    if PROBLEM:
        say("skipped: %s" % PROBLEM)
        continue
    fire()
