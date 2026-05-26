# ADR 0010: Module Layout

## Decision

`gc.py`, `verify.py`, and `metadata.py` are first-class modules with the
operational logic they describe. `repository.py` remains the user-facing façade
that acquires locks and delegates to those modules.

## Reason

An early project sketch listed separate modules for GC, verification, and
metadata restoration. Keeping thin re-export shims would satisfy imports but
hide where behavior lives. Colocating the logic matches the documented layout
without splitting the public `Repository` API.

## Non-goals

Further decomposition (for example a standalone `metadata.py` CLI) is out of
scope unless restore behavior grows substantially.
