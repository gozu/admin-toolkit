# Whole-task Cobuild comparison

56 of 64 have matching new measurements. Median Cobuild / Mesh time: 0.98× across matching pairs. Other rows retain explicit pending, failed or excluded status.

Both paths receive the same ADTK instructions, tool catalog and task. Each model chooses its tools and produces the final answer. ADTK retains tool execution, permissions and confirmation checks. Timings include model calls and execution; fixture creation/reset and independent verification are outside the clock. Each pair runs Mesh then Cobuild once, so cache/order effects and model variability remain. Functional matching does not verify every sentence of the final answer. The model column is the configured main model; Cobuild may internally route service calls. Historical operation-bridge passes do not count. This is the comparison suite, not a live chat routing change.

See the adjacent CSV, JSON and searchable HTML for all 64 checks.
