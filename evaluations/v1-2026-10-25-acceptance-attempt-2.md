# Real acceptance follow-up: stopped after twenty-three exchanges

**G4 did not pass. Further paid campaigns have stopped under the frozen spending rule.** This follow-up completed twenty-three actual received/generated/delivered history exchanges and their current-input encoding. The last historical comparison timed out with unknown provider usage. The remaining 77 history exchanges and all twelve isolated probes were not executed. The product is not accepted as a mature first release.

## Candidate and checkpoint

The frozen candidate was `0f2685608c2402922f5cd47e8bac55856e82e166`, runtime build `7e40bd17ef4cdc6c894d620ea244788c34f27874ab4422072a212d169b537b13`, with twenty-two unchanged migrations. The source archive SHA-256 is `25b76bd10e03b77208e4f8eb3a39b4ebc5793f48cc234d34fbb27902e5eb297f`; the canonical plan digest is `bc8dfdf26f5e04fbbf2b7debaecd1e41312148fb6e95462c52e6756794b93d5f`.

Only code fingerprints changed from the first attempt's plan. The model (`deepseek-v4-pro`, thinking enabled), 100 history inputs, twelve probe questions and oracle, memory-only arm, context limits and spending ceilings stayed frozen. The [local repair verification](v1-2026-10-25-link-coverage-repair.md) remains the installation/regression prerequisite evidence; its full-suite fixture failure and successful follow-up remain disclosed. This campaign adds no baseline or paid answer reviewer.

After history-015 the same process paused with 62 known calls and 759,924 confirmed tokens. Two source-and-answer audits and an independent accounting audit reviewed all sixteen exchanges. Plan, stage and ledger digests matched the checkpoint; no new calls occurred during the pause. Reconstructed clients/runtime replayed the completed turn with identical sources and output, without generation or delivery calls. The checkpoint was explicitly released, and history-016 continued in the same ledger. This checkpoint is an early review boundary, not final acceptance.

## Failure at history-022

The twenty-third input reported a city-sketch note labeled `云井1658` on page 5. The actual response and current-input encoding completed, publishing two attributed semantic fragments and an inferred interaction group. The subsequent historical proposal received HTTP 200 response headers but no observed response-body bytes. The transport recorded `stream_error` after **240.533692 seconds**; the cognitive callback recorded `SemanticProviderError` with code `timeout` after **240.543790 seconds**. No proposal JSON, verification result or usage arrived. There was no historical publication.

The evaluator stopped with `unknown_provider_usage`; `no_learning_execution_failure` is false. An HTTP success status does not establish a completed response. The evidence supports a response-body timeout, but cannot identify whether the cause was the provider or network, establish that it was transient, or justify a parser change or automatic retry.

The server-side settlement also failed. The saved job remains `running` with an inactive lease and `last_error=null`; its execution error is `provider_uncertain`. Its operation remains `reserved` with unknown usage. It must not be described as cancelled or successfully settled as uncertain. The retained reservation charges one call and **82,714 semantic input bytes**; the evaluator retains **16,384 reserved output tokens**. The latest lease lasted 245 seconds, but the archive lacks the settlement-attempt timestamp and original exception. A later expired-lease snapshot does not prove why settlement failed.

The frozen `no_unsettled_retry-v1` claim path fences an expired job with an unsettled operation before another attempt or provider call. The failed database was preserved without reclaiming or retrying it. Two monitoring gaps were nevertheless confirmed: the three admitted objectives were reported as zero unassessed before a coverage result existed, and the expired job was reported as runnable despite its unsettled reservation. Those are observability defects, not evidence of a repeated paid call. The [subsequent local repair](v1-2026-10-25-timeout-monitoring.md) cannot change this campaign's result.

## Actual usage and reviewed scope

| Measurement | Observed |
| --- | --- |
| Delivered history / successfully encoded new inputs | 23 / 23 |
| Probes / paid reviewers / baseline calls | 0 / 0 / 0 |
| Provider reservations | 91: 23 host and 68 cognitive |
| Terminal ledger events with known / unknown usage | 90 / 1 |
| Confirmed prompt / completion tokens | 766,041 / 303,910 |
| Confirmed total tokens | 1,069,951, plus unknown usage from the last call |
| Reserved output allowance | 1,208,320 tokens |
| Maximum root calls / input bytes | 4 / 185,103 |
| Maximum phase calls / input bytes | 2 / 122,568 |

All reservations stayed within the frozen root, phase and aggregate ceilings. The 91 terminal ledger events include the failed stream; they are not 91 successful or known-usage calls. The unknown call is not zero-cost. Server cognitive accounting separately retains 67 completed operations and one reserved operation, with `counts_complete=false`.

Independent semantic review covered all twenty-three delivered answers and their new publications: 51 semantic memories, 22 inferred episode groups, three distinct abstraction relations and one current-only update. No unsupported usable assertion or answer was found in that scope. The three relations cover six selected objectives; relation counts and objective outcomes are distinct. The new relation-coverage contract handled actual multi-target links, including stages006,011,021. This is evidence for the repaired path, not a successful repetition of the previously rejected response.

The optional stage005 episode proposal remained rejected while required current encoding succeeded. The generic pronoun in one stage010 fragment is not demographic evidence. All inspected prior-episode navigation arrays were empty, and cross-conversation funding was not exercised. These acknowledgment-heavy stages do not establish performance on the unexecuted recall/change probes.

## Retained evidence and remaining work

The [machine-readable summary](v1-2026-10-25-acceptance-attempt-2.json) records usage, charged failure, raw monitor state, candidate identity and hashes of the private plan, report, ledger, checkpoint decisions and independent audits. The original artifacts remain under `.local/v1-acceptance-followup-0f26856`. The source audit requested for stages024–031 explicitly records that none ran; it is not a semantic pass. The [first failed attempt](v1-2026-10-25-acceptance-attempt-1.md) is unchanged.

This was the single permitted full follow-up under the [finite acceptance criteria](../architecture/v1-acceptance.md). It has exited and cannot resume paid work. Offline diagnosis and targeted local repairs remain possible; a third paid campaign is not authorized by the exhausted follow-up allowance. Complete 100-exchange/twelve-probe acceptance, end-history replay and final release acceptance remain unpassed. Installation/performance evidence for earlier verified artifacts is preserved with its original scope.
