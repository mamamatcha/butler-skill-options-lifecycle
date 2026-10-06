# Changelog

## 1.0.0

- An assigned put is reported as "you agreed to buy", not "you have bought":
  Derive settles in cash, so the owner owns nothing until the delivery buy fills.
  Only the delivery path asserts ownership.
- The assignment cost is stated as what it is - how much more than market the
  owner agreed to pay - rather than as a price gap in the wrong direction.

- First release. Watches one yield note: heads-up before expiry, outcome at
  settlement, optional post-settlement purchase of the asset.
- Reads Derive's public API only, so the lifecycle does not depend on the options
  rail and survives a session-key rotation.
- Distinguishes cash settlement from delivery: an assigned put is reported as a loss
  held in cash unless `DELIVER_ASSET` is on and the spot buy filled.
- Expiries parsed as 08:00 UTC with `calendar.timegm`, so a container in a DST zone
  cannot call settlement an hour early.
