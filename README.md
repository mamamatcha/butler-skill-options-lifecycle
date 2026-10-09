# options-lifecycle

Watches one Derive option from the fill to the outcome: a note the owner **sold** fully collateralised (`acp options open`), or an option they **bought** (`acp options buy`). The options-trading skill files it once the fill is confirmed, one duty per option, with the fill's own numbers as settings.

## How it is filed

Filed with `recipe: "options-lifecycle@4"` only after the open is confirmed (`bevo-read request <key> --route options` reads `approvalStatus: confirmed`). The settings are copied from its `approvalOutcome`, never from the quote: `INSTRUMENT` and `STRIKE` as filled, `SIZE` = `filledSize`, `PREMIUM_USD` = `netPremiumUsd` for a sold note or `totalCostUsd` for a bought option, `COLLATERAL_USD` = `collateral` (0 for a bought option). Any asset Derive lists options on works. `TOKEN_ID` is filed only with `DELIVER_ASSET`, pinning the token the delivery buy trades from its `/token-search` row (`native:8453` for ETH on Base). A note whose open is unknown, refused or pending gets no duty.

## What it does

| When | The owner gets |
| --- | --- |
| `HEADS_UP_HOURS` before expiry, once | where the asset is on Derive's own index, and which way the note is leaning |
| after the 08:00 UTC expiry, once Derive publishes the price | a sold note: what they kept, or what they agreed to buy or sell; a bought option: what Derive paid out against what it cost, or that it expired worthless; then the duty finishes |
| after an assigned put, only with `DELIVER_ASSET` on | a spot buy of the underlying, through `acp trade`, keyed `note:<duty>:deliver` |

Expiry and option type are read back out of `INSTRUMENT` (`ETH-20261030-2450-P` expires 2026-10-30 08:00 UTC) and cross-checked against `PRODUCT` and `UNDERLYING`; a mismatch stops the duty rather than guessing.

## What it will not do

- **Say "you now own" without a filled buy.** Derive settles in cash: an assigned put leaves the owner holding the loss in USDC, not the asset. Only an executed delivery buy changes that, and a pending, refused or unknown one is reported as such.
- **Guess a settlement.** No price published yet means it waits quietly; past 6 hours it says so once. An unreadable settlement read is reported as the read being broken, never as an outcome.
- **Retry the delivery buy.** It has one key; an unclear answer is checked with `exec_status`, never re-sent.
- **Price off a DEX, or buy by ticker.** The heads-up reads Derive's index for this option (`/options/ticker`), the price it settles against; an empty read skips the heads-up for that tick. The delivery buy trades only the token pinned in `TOKEN_ID`, on its own chain.
- **Move collateral.** After settlement the collateral sits free in the Derive account; withdrawing it is a separate `acp options withdraw`.
- **Watch an option that was closed early.** A bought option sold back, or a sold note bought back, finishes the duty quietly at the heads-up (it checks `/options/account`); the skill normally deletes the duty on the close.
- **Open, roll or close anything.** It only watches the one it was filed for.

## Settings

| Name | Unit | Means |
| --- | --- | --- |
| `INSTRUMENT` | Derive name | the option sold, exactly as filled |
| `PRODUCT` | `cash_secured_put` \| `covered_call` (sold) \| `long_call` \| `long_put` (bought) | must match the `P`/`C` in `INSTRUMENT` |
| `UNDERLYING` | ticker | the prefix of `INSTRUMENT`: any currency Derive lists options on |
| `TOKEN_ID` | pin, only with `DELIVER_ASSET` | `native:<chainId>` or `<address>:<chainId>`: the one token the delivery buy trades |
| `STRIKE` | US dollars | the price agreed to buy (put) or sell (call) at |
| `SIZE` | contracts = units of the underlying | as filled |
| `PREMIUM_USD` | US dollars | sold: kept, net of Derive's fee; bought: paid, fee included |
| `COLLATERAL_USD` | US dollars | USDC locked behind a sold put; `0` otherwise |
| `DELIVER_ASSET` | on/off, default **off** | buy the `TOKEN_ID` token after an assigned put, spending SIZE x the settlement price of wallet USDC on its chain; needs `TOKEN_ID` |
| `HEADS_UP_HOURS` | hours, default `24` | how long before expiry the heads-up goes out |

## Trigger

`{"kind": "timer", "intervalSeconds": 900}`. Settlement is usually published within minutes of 08:00 UTC, so a 15-minute timer reports it within the half hour.
