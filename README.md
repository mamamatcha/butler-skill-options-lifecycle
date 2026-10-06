# butler-skill-options-lifecycle

The `options-lifecycle` duty template for [Butler](https://github.com/Virtual-Protocol/butler-skills).
Filed by the `yield-notes` skill, one duty per open note.

## What it does

Watches a single yield note from open to outcome:

| When | What the owner gets |
| --- | --- |
| `HEADS_UP_HOURS` before expiry | where the asset is, and which way the note is leaning |
| at settlement | what they kept, or what they have just bought — with the number |
| after an assigned put | the asset itself, if `DELIVER_ASSET` is on |

## Why it needs no options rail

Every read is Derive's **public** API. The note's details arrive as settings when the
duty is filed, and Derive publishes settlement prices openly
(`public/get_option_settlement_prices`), so the outcome is computable with no key, no
session and no server-side rail.

That matters twice: the lifecycle works *before* the rail exists, and it keeps
working if a session key is ever rotated or revoked. The only money path is the
optional delivery buy, which goes through `acp trade` — an existing money command
with its own approval card and idempotency key.

## The one thing it must never get wrong

**Derive settles in cash.** An in-the-money put reduces USDC; it does not hand over
ETH. So the duty only says "you have bought 2.04 ETH" when `DELIVER_ASSET` is on and
the spot buy has actually been filed. Otherwise it says the owner is holding the
loss and offers to buy — because that is what has happened.

## Settings

See `recipe.json`. `INSTRUMENT` must be the exact Derive name that was filled
(`ETH-20261030-2450-P`) — expiry and option type are read back out of it, and
cross-checked against `PRODUCT` and `STRIKE`. A mismatch stops the duty rather than
guessing.

`PREMIUM_USD` must be the **net** figure after Derive's fee, because that is the
number quoted back to the owner at expiry.

`DELIVER_ASSET` defaults to **off**: it spends money, so the owner has to have asked.

## Validating a change

Run the hub's duty validator against this directory. A duty is checked harder than a
skill: imports are limited to the SDK plus a small stdlib allowlist, there is no
network of its own, and only `acp trade`, `acp wallet` and `acp card` may move money.
