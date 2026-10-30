# Discovery boundary and implementation

Owner requested implementation on 2026-10-30. H-003 reviewed outcomes cannot be
reused as OOS evidence for H-0004. Trading remains paused. This work adds offline
observation commands; it does not deploy/restart the loop or alter H-003 semantics.

Implemented: read-only audit, hash-pinned manifests, quote-aware replay, equal
normalized-risk treatment constraints, coverage accounting and paired daily
reports. A trusted read-only quote/packet collector and isolated model invocations
are not yet implemented. Review them together with source timestamps and costs
before making a prospective claim.

Q-15 covers exact source/scope, comparator and criteria. Filling placeholders is
necessary before freeze. A `reviewed_by` field is not strategy-stage approval.
