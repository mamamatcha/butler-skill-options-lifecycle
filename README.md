# options-lifecycle

Watches one yield note on Derive, a single option the owner sold fully collateralised, from the fill to the outcome. The yield-notes skill files it once `acp options open` has confirmed the fill, one duty per note, with the fill's own numbers as settings.

## How it is filed

Filed with `recipe: "options-lifecycle@2"` only after the open is confirmed (`bevo-read request <key> --route options` reads `approvalStatus: confirmed`). The settings are copied from its `approvalOutcome`, never from the quote: `INSTRUMENT` and `STRIKE` as filled, `SIZE` = `filledSize`, `PREMIUM_USD` = `netPremiumUsd`, `COLLATERAL_USD` = `collateral`. `TOKEN_ID` pins the underlying from its `/token-search` row (`native:8453` for ETH on Base). A note whose open is unknown, refused or pending gets no duty.

## What it does

| When | The owner gets |
| --- | --- |
| `HEADS_UP_HOURS` before expiry, once | where the asset is, and which way the note is leaning |
| after the 08:00 UTC expiry, once Derive publishes the price | what they kept, or what they agreed to buy or sell, with the numbers; then the duty finishes |
| after an assigned put, only with `DELIVER_ASSET` on | a spot buy of the underlying, through `acp trade`, keyed `note:<duty>:deliver` |

Expiry and option type are read back out of `INSTRUMENT` (`ETH-20261030-2450-P` expires 2026-10-30 08:00 UTC) and cross-checked against `PRODUCT` and `UNDERLYING`; a mismatch stops the duty rather than guessing.

## What it will not do

- **Say "you now own" without a filled buy.** Derive settles in cash: an assigned put leaves the owner holding the loss in USDC, not the asset. Only an executed delivery buy changes that, and a pending, refused or unknown one is reported as such.
- **Guess a settlement.** No price published yet means it waits quietly; past 6 hours it says so once. An unreadable settlement read is reported as the read being broken, never as an outcome.
- **Retry the delivery buy.** It has one key; an unclear answer is checked with `exec_status`, never re-sent.
- **Price or buy by ticker.** The underlying is the token pinned in `TOKEN_ID` at filing; the heads-up prices that pin with `/token-stats` and the delivery buy trades it on its own chain. An empty price read skips the heads-up for that tick.
- **Move collateral.** After settlement the collateral sits free in the Derive account; withdrawing it is a separate `acp options withdraw`.
- **Open, roll or close a note.** It only watches the one it was filed for.

## Settings

| Name | Unit | Means |
| --- | --- | --- |
| `INSTRUMENT` | Derive name | the option sold, exactly as filled |
| `PRODUCT` | `cash_secured_put` \| `covered_call` | must match the `P`/`C` in `INSTRUMENT` |
| `UNDERLYING` | ticker | `ETH`, `BTC`; the prefix of `INSTRUMENT` |
| `TOKEN_ID` | pin | `native:<chainId>` or `<address>:<chainId>`: the one token priced and, on delivery, bought |
| `STRIKE` | US dollars | the price agreed to buy (put) or sell (call) at |
| `SIZE` | contracts = units of the underlying | as filled |
| `PREMIUM_USD` | US dollars | net of Derive's fee |
| `COLLATERAL_USD` | US dollars | USDC locked behind a put; `0` for a call |
| `DELIVER_ASSET` | on/off, default **off** | buy the `TOKEN_ID` token after an assigned put, spending SIZE x the settlement price of wallet USDC on its chain |
| `HEADS_UP_HOURS` | hours, default `24` | how long before expiry the heads-up goes out |

## Trigger

`{"kind": "timer", "intervalSeconds": 900}`. Settlement is usually published within minutes of 08:00 UTC, so a 15-minute timer reports it within the half hour.
