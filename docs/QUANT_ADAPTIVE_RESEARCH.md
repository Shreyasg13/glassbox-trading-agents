# Quant shadow research acceptance criteria

Evaluate each arm against a frozen control using timestamped inputs and a
separate research ledger. Fit transformations using only data finalized by the
decision timestamp. Keep the data revision and the exchange calendar in the
run identity so later price corrections do not silently change prior results.

## Delayed outcomes

Use an authoritative calendar for the seven subsequent completed sessions.
A missing price on the target session leaves the label pending. Reject
historical ranking snapshots that include labels available only after the
snapshot cutoff. Record prediction, label and selection as distinct events.

## Baselines and decisions

Fit the risk event-rate baseline on earlier observations and score it alongside
each arm on the same held-out dates. Thirty distinct matured decision dates is
a minimum sample gate. Require a conservative interval on paired improvement
using date-level dependence and a correction for the number of arms compared.
Record ranking probabilities as diagnostics. Log actual selection propensity
only when a selection policy samples an arm.

Use residual cointegration critical values for Engle-Granger. Validate missing
bars and negative controls. Keep CIR out of the evaluation until a timestamped
rate source is available. Treat Heston as unvalidated until calibration data
and out-of-sample diagnostics are available.

## Integration gate

First run walk-forward, deterministic replay, missing-bar, migration and
duplicate-delivery tests against an offline store. Validate the flag defaults
off and exercise existing paper, committee, publication and compliance tests
before connecting any stage. Keep the existing 60-day research evidence rule.
This document adds no executable stage or migration. Roll back by reverting
the documentation commit.
