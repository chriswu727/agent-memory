# First real acceptance attempt: stopped after twelve exchanges

**G4 did not pass.** The frozen memory-only campaign stopped after twelve actual received, generated and delivered history exchanges (`history-000`–`history-011`). It reached neither the sixteen-exchange checkpoint nor any of the twelve isolated probes. The planned 100-exchange/12-probe acceptance remains incomplete; this is not a mature-release claim.

## Frozen candidate and scope

The candidate was commit `33cff1f19e9cb7f0e687dc19040a4bc50ace2372`, with runtime build identity `0c770b1de1ee3c0cfc4ed8e3c3f7a1869f348ebf9d5046e81b79b8fd9c92d520` and twenty-two migrations. Its frozen source archive SHA-256 is `4ae5097bb00aef4dd7fae9519036cd12f3dfeca25c8ac1fc5fa6456e442ce8d3`; the canonical plan digest is `2890ea86189a2e7cf4f50673fed4336a9c3b1b38bf2428950b09d26007a371eb`.

The plan used `deepseek-v4-pro`, thinking enabled, on-demand orientation and only the memory arm. It froze 100 history exchanges, twelve isolated probes, a same-ledger checkpoint after sixteen exchanges, and durable replay checks after exchanges sixteen and one hundred. No baseline or paid answer-review provider ran. None of the checkpoint, replay or probe checks was reached.

The [local acceptance checkpoint](v1-2026-10-25-local-acceptance.md) remains the evidence for installed-wheel/Docker flows, regression results and local performance. The [artifact verification](v1-2026-10-25-artifact-verification.json) records the exact matching packaged runtime. Those checks are not rerun or relabeled here, and they do not substitute for G4. This campaign used that unchanged candidate, not a later repair.

## What failed at history-011

The twelfth exchange's delivery and current-input encoding completed. The user reported a music note with tag `云井2358` on page 3. The assistant correctly acknowledged it and added a source-supported, unsolicited reminder about the earlier sixth music note (`云井1841`, page 6). The encoding job published its two attributed assertions and one inferred interaction group.

The subsequent history-integration response was internally inconsistent. Proposal `link-0001` declared an `abstracts` relation with two target memory versions. The decision for one target cited that link as `proposed`, while the decision for the other target declared `no_change` with reason `already_represented` and no link IDs. A target cannot simultaneously participate in that proposed relation and be recorded as having no proposed change. The [sanitized evidence summary](v1-2026-10-25-acceptance-attempt-1.json) preserves the exact target/objective mapping and response fields.

Strict validation correctly rejected the response. The history job consumed one known provider call, ended `cancelled` with `last_error=invalid_proposal` and processing status `invalid_response`, and published no history claims or relations. All three selected comparison objectives remain `not_evaluated`; they are not credited as assessed no-change. No verification call or paid retry followed that invalid proposal.

The evaluator stopped with `cognitive_processing_unavailable` after `no_learning_execution_failure` became false. This was a failed required processing check, not a provider transport failure, unknown usage, budget overrun or false-memory publication. Monitoring retained the three unassessed objectives and one earlier deferred history window: two open history windows, zero open encoding windows and zero unknown monitor counts. The failed response, charged call and pending work remain preserved.

## Actual usage and machine checks

| Measurement | Observed |
| --- | --- |
| History exchanges delivered | 12 |
| Probes / baseline calls / paid reviewer calls | 0 / 0 / 0 |
| Provider calls | 47: 12 host and 35 cognitive |
| Known settled usage / unknown usage / calls in flight at stop | 47 / 0 / 0 |
| Prompt tokens | 411,579 |
| Completion tokens | 175,189 |
| Total tokens | 586,768 |
| Maximum charged calls / input bytes for one finished authorization root | 4 / 232,474 |
| Maximum calls / input bytes for one executed phase job | 2 / 168,091 |

Observed roots stayed within four calls and 393,216 input bytes; phase jobs stayed within two calls and 196,608 bytes. The frozen aggregate ceiling was 736 calls and 10,682,368 reserved output tokens; actual reserved output allowance was 622,592 tokens, distinct from consumed completion tokens. The accounting audit's recorded failure was the stage011 pipeline check. These machine checks establish bounded execution and accounting for this attempt, not semantic accuracy or complete historical coverage.

## Independent review and retained evidence

Two independent source-and-answer audits reviewed all twelve delivered exchanges and their newly published semantic artifacts. They found no unsupported factual promotion in the reviewed published memories or delivered answers. Across their scopes they reviewed 26 semantic memories, twelve inferred episode organizations, two belief relations and one accepted current-only context update. This finding does not turn the rejected stage011 response into a pass.

The sampled abstractions retained their limited scope; repeated note topics were not treated as corrections. Episode organization labels were treated as fallible audit/navigation metadata rather than factual evidence. All inspected prior-episode navigation collections were empty, so this run did not exercise reuse of a nonempty prior group. The second audit covered only stages008–011: its requested stages012–015 never ran.

| Private audit | Actual scope | SHA-256 |
| --- | --- | --- |
| `audit-history-000-007.json` | 000–007 | `c56cab60a5c7f1bd817449b03178da27139e9734b4944a999f39483ab0576077` |
| `audit-history-008-015.json` | 008–011 | `e8fa529a42c353fabf9bf9622c7708758dc9b781ae6360457135f72acedd3109` |

The original plan, source archive, provenance, reports, call ledger and audits are retained under the private run directory `.local/v1-acceptance-33cff1f19e`. Their hashes are recorded in the public JSON summary. The private launcher emitted Python's duplicate-module `runpy` warning because the evaluator module had already been imported before execution. The exact frozen package was verified; the warning was not the recorded runtime failure. It remains disclosed rather than erased from the log.

## Next boundary

The relation-to-objective accounting repair was pending when this attempt stopped. The subsequent [local repair verification](v1-2026-10-25-link-coverage-repair.md) records a new version and deterministic checks; it does not change this attempt's outcome or establish a successful rerun. This attempt has not been retried, resumed for paid work or overwritten.

Under the [finite acceptance criteria](../architecture/v1-acceptance.md), at most one newly frozen full follow-up campaign remains available after a concrete verified repair. If that campaign also fails, further paid campaigns stop for offline reassessment. The remaining 88 history exchanges, checkpoint/replay checks and all twelve probes are unexecuted evidence, not implied passes.
