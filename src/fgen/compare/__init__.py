"""Variant sets, a parallel runner and comparison tables (plan-phase9 task B).

The agent's reliable strength is not inventing a layout but opening several candidates
at once, reading hard metrics side by side and choosing one. This package makes that
the default shape of every choice: a **variant file** (``config/variants/*.json``) names
a generator family, a base parameter set and the members to try (explicit overrides
and/or a grid); ``fgen compare FILE`` generates every member in its own process with
its own budget, and writes one table (``compare/<name>.{jsonl,md}``) of nothing but
hard metrics: feasible or not and why, seconds to the first period, period size, poles,
inserters, cost, predicted throughput, blueprint hash. A choice that is committed
(a pinned parameter, a decision file) points at the row it was read from.

Modules: :mod:`fgen.compare.variants` (files, expansion, families),
:mod:`fgen.compare.runner` (processes, ranking), :mod:`fgen.compare.table` (output).
"""
