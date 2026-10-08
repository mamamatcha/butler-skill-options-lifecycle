# Changelog

## 2

- The underlying is pinned at filing in the new required `TOKEN_ID`
  (`native:<chainId>` or `<address>:<chainId>`), replacing `CHAIN_ID`. The
  heads-up prices exactly that pin with `/token-stats` (`/token-price` is
  retired, and v1 called it with `symbol`, got a 400 and never sent a heads-up);
  an empty price read skips the tick.
- The delivery buy uses the `acp trade` grammar bevo-server parses
  (`--token-in usdc --amount-in <usd> --token-out <asset>`), spending SIZE x the
  settlement price, of the `TOKEN_ID` token on its own chain. v1's
  `acp trade buy … --amount` was unparseable.
- `acp` answers are read the way copytrade reads them: `executed`, `asked`/`ok`,
  `unrecognized`; an unreadable answer is unknown, checked with `exec_status`,
  never retried.
- Notes tell and never ask: a duty cannot hear a reply. The outcome is the
  duty's last word through `bevo.done()`; failed reads go to `bevo.fail()`.
- `UNDERLYING` is cross-checked against `INSTRUMENT`.
- The settlement read is the options rail (`/options/settlement`) on Derive v3.
- README rewritten in the hub's template format; tests and scenario fixtures added.

## 1

- First release. Watches one yield note: heads-up before expiry, outcome at
  settlement, optional post-settlement purchase of the asset.
- An assigned put is reported as "you agreed to buy", not "you have bought":
  Derive settles in cash, so the owner owns nothing until the delivery buy fills.
- Expiries parsed as 08:00 UTC, so a container in a DST zone cannot call
  settlement an hour early.
