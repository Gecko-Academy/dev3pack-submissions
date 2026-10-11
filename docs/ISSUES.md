# Ranked issues

**Filled by:** session 9 (the first list, `cap01-e5`), kept current until
session 14, which fixes rank 1 and adds its regression test.

| rank | issue | impact |
|---:|---|---|
| 1 | The evaluator accepts answers whose citations do not support the claim: a fake agent that cites everything and lies passed 50% of the eval. | High: the metrics look healthy while users can receive false answers dressed up with decorative citations. |
| 2 | Retrieval is lexical only, so it misses relevant passages that use different words than the question. | Medium: the agent refuses or answers worse on questions that do have an answer in the sources. |
| 3 | The early-refusal threshold is not calibrated on enough data. | Medium: it may refuse answerable questions or let through questions that have no evidence. |

## Rank 1, in progress

- The fix: to be done in session 14.
- The regression test: to be added in session 14 under tests/.
- Before and after: see [EVAL_REPORT.md](EVAL_REPORT.md).
