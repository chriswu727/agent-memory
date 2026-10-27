export type Identifier = string;

export type HistoryFundingSummary = {
  version: "private-brain-history-funding-v1";
  authorization_root_job_id: Identifier;
  origin_job_id: Identifier;
};

export type HistoryAttentionSummary = {
  version: "bounded-comparison-attention-v1";
  limit: 3;
  candidate_count: number;
  admitted_count: number;
  not_selected_count: number;
  focus_count: number;
  exploration_count: number;
  source_origin_count: number;
  retrieval_truncated: boolean;
  search_exhaustive: false;
  discovery_limitations: ("cue_limit" | "candidate_scan_limit" | "candidate_limit" | "source_candidate_limit")[];
};

export type LearningHistorySummaries = {
  history_funding?: HistoryFundingSummary;
  history_attention?: HistoryAttentionSummary;
};

export type OrientationReason =
  | "existing_orientation" | "recall_plan" | "embedding_recall" | "historical_boundary_required"
  | "conditional_memory" | "conditional_relation" | "working_state" | "incomplete_dependencies"
  | "no_contextual_dependencies" | "recall_controller" | "assessment_unavailable";

export type OrientationAssessment = {
  version: "orientation-route-v1";
  decision: "orient" | "skip";
  reasons: OrientationReason[];
  input_fingerprint: string;
  dependency_fingerprint: string;
  snapshot: {
    scope_generation: number;
    dependency_generation: number;
    durable_sequence: number;
    indexed_sequence: number;
    working_revision: number;
  };
};

export type OrientationRoute = {
  version: "orientation-route-v1";
  policy: "always" | "on_demand";
  decision: "orient" | "skip";
  reasons: OrientationReason[];
  assessment: OrientationAssessment | null;
};

export type RuntimeOrientationState = { orientation_route: OrientationRoute | null };

export type PreparedMemorySelection = {
  version: "prepared-memory-selection-v1";
  state: "complete" | "unavailable";
  unavailable_reason: "selection_limit" | "selection_unverifiable" | null;
  turn_id: Identifier;
  brain_id: Identifier;
  conversation_id: Identifier;
  branch_id: Identifier;
  packet_id: Identifier;
  model_context_sha256: string;
  context_fingerprint: string;
  context_generation: number;
  working_revision: number;
  prepared_at: string;
  input_event_refs: { event_id: Identifier; revision: number }[];
  source_event_refs: { event_id: Identifier; revision: number; content_sha256: string }[];
  memories: {
    memory_id: Identifier;
    memory_revision: number;
    statement_sha256: string;
    trace_id: Identifier;
    recall_id: Identifier;
    evidence_refs: EvidenceRef[];
  }[];
} | {
  version: "prepared-memory-selection-v1";
  state: "unavailable";
  unavailable_reason: "custom_context";
};

export type RuntimePreparedSelectionState = { prepared_memory_selection: PreparedMemorySelection | null };

export type ResponseReviewIssue =
  | "current_input_misread" | "unsupported_memory_claim" | "applicability_override" | "uncertainty_erased"
  | "unconfirmed_action" | "uncertain_current_input_fidelity" | "uncertain_memory_fidelity"
  | "uncertain_applicability" | "uncertain_uncertainty" | "uncertain_action_claims"
  | "incomplete_review" | "review_unavailable";

export type ResponseReview = {
  status: "pending" | "approved" | "rejected" | "inconclusive";
  policy_fingerprint: string;
  request_fingerprint: string;
  draft_sha256: string;
  context_fingerprint: string;
  context_generation: number;
  working_revision: number;
  issue_codes: ResponseReviewIssue[];
  not_a_truth_certificate: true;
};

export type RuntimeResponseReviewState = {
  response_review_policy: string | null;
  response_review: ResponseReview | null;
  response_review_calls: number;
};

export type TimeRange = {
  "start": string | null;
  "end": string | null;
  "precision": "exact" | "day" | "month" | "year" | "approximate" | "unknown";
  "basis": "source_explicit" | "source_relative_resolved" | "inferred" | "unknown";
};

export type SystemTime = {
  "recorded_at": string;
  "superseded_at": string | null;
};

export type ScopeSelector = {
  "brain_ref": Identifier;
  "conversation_ref"?: Identifier;
  "branch_ref"?: Identifier;
  "collection_refs"?: (Identifier)[];
};

export type BoundScope = {
  "tenant_id": Identifier;
  "brain_id": Identifier;
  "principal_binding_id": Identifier;
  "conversation_id": string | null;
  "branch_id": string | null;
  "visibility": "private" | "conversation" | "explicit_shared";
  "access_policy_id": Identifier;
  "policy_revision": number;
  "scope_generation": number;
};

export type EvidenceRef = {
  "event_id": Identifier;
  "event_revision": number;
  "span"?: {
  "start": number;
  "end": number;
  "unit": "unicode_code_point";
};
  "source_content_sha256": string;
  "relationship": "supports" | "contradicts" | "corrects" | "context_only";
};

export type EventContent = {
  "media_type": "text/plain" | "application/json" | "artifact/reference";
  "text"?: string;
  "structured"?: Record<string, unknown>;
  "artifact_ref"?: Identifier;
};

export type ObservedEventInput = {
  "source_event_key": Identifier;
  "source_revision": number;
  "producer_ref": Identifier;
  "actor_ref": Identifier;
  "actor_role": "user" | "assistant" | "tool" | "operator" | "system" | "external";
  "event_kind": "message" | "tool_result" | "artifact" | "feedback" | "message_edit" | "delivery_receipt" | "source_correction";
  "content": EventContent;
  "occurred_time": TimeRange;
  "received_at_client"?: string | null;
  "interpretation_hint": "real_world_report" | "quotation" | "roleplay" | "hypothetical" | "dream" | "unknown";
  "delivery_state": "received" | "delivered" | "partially_delivered" | "draft" | "cancelled" | "unknown";
  "parent_source_event_keys"?: (Identifier)[];
  "speech_act_hint"?: "direct_statement" | "inquiry" | "presupposition" | "correction" | "confirmation" | "instruction" | "unknown" | "not_applicable";
};

export type Event = {
  "event_id": Identifier;
  "revision": number;
  "sequence": number;
  "scope": BoundScope;
  "observed": ObservedEventInput;
  "system_time": SystemTime;
  "content_sha256": string;
  "origin": "user_statement" | "assistant_output" | "tool_observation" | "document_import" | "external_message" | "operator_correction" | "system_observation";
  "origin_verification": "verified_producer" | "unverified_import" | "unknown";
  "previous_revision": number | null;
  "utterance_segments": (UtteranceSegment)[];
};

export type MemoryDynamics = {
  "activation": number;
  "retention_priority": number;
  "detail_level": "verbatim" | "episode" | "gist" | "index_only";
  "last_external_reinforcement_at": string | null;
  "last_recalled_at": string | null;
  "decay_policy_version": Identifier;
  "user_pinned": boolean;
  "reasons": ("explicit_user_request" | "validated_outcome" | "external_repetition" | "distinctive_event" | "open_commitment" | "current_relevance" | "default_policy")[];
};

export type TypedValue =
  | { type: "concept"; id: string }
  | { type: "string"; value: string }
  | { type: "number"; value: number }
  | { type: "boolean"; value: boolean };

export type TypedPredicateRef = { id: string; version: number };
export type TimeBound = { kind: "instant"; at: string } | { kind: "unknown" | "unbounded" };
export type ValidDuring = { kind: "unknown" } | {
  kind: "interval"; start: TimeBound; end: TimeBound;
  anchor?: { event_id: string; revision: number; timestamp: string; timezone: string; expression: "today" };
};
export type ContextObservation = {
  observation_id: string;
  subject_ref: string;
  predicate_ref: TypedPredicateRef;
  value: TypedValue;
  polarity: "positive" | "negative";
  valid_during: ValidDuring;
  evidence_refs: (EvidenceRef & {
    relationship: "supports";
    span: { start: number; end: number; unit: "unicode_code_point" };
  })[];
};

export type MemoryApplicability = {
  value: "true" | "false" | "unknown";
  status: "true" | "false" | "unknown" | "conflict";
  conflict: boolean;
  supporting_observation_ids: string[];
  opposing_observation_ids: string[];
  unresolved_observation_ids?: string[];
  evidence_refs: EvidenceRef[];
  reasons: string[];
  registry_fingerprint: string;
  evaluated_at: string;
};

export type SemanticCondition =
  | { "op": "always" }
  | { "op": "unresolved"; "label": string }
  | { "op": "holds"; subject_ref: string; predicate_ref: TypedPredicateRef; value: TypedValue; polarity?: "positive" | "negative" }
  | { "op": "eq" | "ne"; "key": string; "value": string | number | boolean | null }
  | { "op": "all" | "any"; "args": SemanticCondition[] }
  | { "op": "not"; "arg": SemanticCondition };

export type MemoryProcedure = {
  "steps": string[];
  "exit_conditions": SemanticCondition;
  "failure_conditions": SemanticCondition;
  "environment": string | null;
  "attempt_event_ids": Identifier[];
};

export type RecallSummary = { statement: string; detail_cues: string[] };

export type MemoryAssertion = {
  "recall_summary"?: RecallSummary;
  "memory_id": Identifier;
  "revision": number;
  "scope": BoundScope;
  "memory_kind": "episodic" | "semantic" | "prospective" | "procedural" | "interaction_preference" | "relationship" | "self_model";
  "statement": string;
  "epistemic_status": "supported_report" | "observed_result" | "derived_inference" | "disputed" | "unknown";
  "world": "real_world" | "quoted" | "roleplay" | "hypothetical" | "dream" | "unknown";
  "evidence_refs": (EvidenceRef)[];
  "valid_time": TimeRange;
  "system_time": SystemTime;
  "lifecycle": "active" | "dormant" | "superseded" | "retracted";
  "supersedes_memory_ids": (Identifier)[];
  "derivation": {
  "method": "direct_capture" | "extraction" | "aggregation" | "human_correction" | "verified_procedure";
  "rule_version": Identifier;
  "model_version": string | null;
  "source_memory_versions": ({
  "memory_id": Identifier;
  "revision": number;
})[];
};
  "dynamics": MemoryDynamics;
  "disclosure_basis": DisclosureBasis;
  "representation"?: "semantic_anchor" | "episode_outline" | "episode_fragment" | "schema" | "procedure";
  "claim_kind"?: "reported_preference" | "reported_fact" | "external_world_fact" | "agent_said" | "proposal_offered" | "commitment_expressed" | "task_completed" | "inferred_pattern" | "procedure";
  "conditions"?: SemanticCondition;
  "valid_during"?: ValidDuring;
  "condition_registry_fingerprint"?: string;
  "counterexamples"?: EvidenceRef[];
  "verification"?: {
    "state": "supported_inference" | "contested";
    "method": "semantic_model";
    "not_a_truth_certificate": true;
  };
  "procedure"?: MemoryProcedure | null;
  "root_event_ids"?: Identifier[];
};

export type WorkingItem = {
  "key": Identifier;
  "value": unknown;
  "knowledge_state": "grounded" | "inferred" | "unknown";
  "evidence_refs": (EvidenceRef)[];
  "expires_at": string | null;
  "updated_at": string;
};

export type OpenLoop = {
  "loop_id": Identifier;
  "description": string;
  "state": "proposed" | "planned" | "in_progress" | "completed" | "cancelled" | "unknown";
  "due_time": TimeRange;
  "evidence_refs": (EvidenceRef)[];
  "last_updated_at": string;
};

export type WorkingState = {
  "scope": BoundScope;
  "revision": number;
  "through_sequence": number;
  "items": (WorkingItem)[];
  "open_loops": (OpenLoop)[];
  "updated_at": string;
};

export type Budget = {
  "max_context_tokens": number;
  "max_model_calls": number;
  "max_embedding_calls": number;
  "max_expansion_steps": number;
  "max_wall_time_ms": number;
  "max_cost_microunits": number;
  "currency": string;
  "token_counting": "exact" | "conservative_estimate";
  "tokenizer_id": string | null;
};

export type Usage = {
  "context_tokens": number;
  "token_counting": "exact" | "conservative_estimate";
  "model_calls": number;
  "embedding_calls": number;
  "expansion_steps": number;
  "elapsed_ms": number;
  "actual_cost_microunits": number | null;
  "reserved_background_cost_microunits": number;
  "currency": string;
  "stopped_by": "sufficient_evidence" | "no_candidates" | "context_limit" | "model_call_limit" | "embedding_call_limit" | "expansion_limit" | "deadline" | "cost_limit" | "unavailable_dependency" | "completed";
};

export type ReadFence = {
  "minimum_durable_sequence": number;
  "consistency": "read_your_writes" | "snapshot" | "best_available";
  "snapshot_revision": number | null;
  "minimum_dependency_generation"?: number;
  "minimum_scope_generation"?: number;
};

export type Coverage = {
  "durable_through_sequence": number;
  "indexed_through_sequence": number;
  "tail_covered_through_sequence": number;
  "snapshot_revision": number;
  "read_fence_satisfied": boolean;
  "search_exhaustive": boolean;
  "limitations": ("approximate_search" | "index_lag" | "source_unavailable" | "budget_exhausted" | "partial_scope" | "deleted_source" | "ambiguous_identity" | "dependency_failure" | "unknown" | "rebuilding")[];
  "dependency_generation": number;
  "projection_generation": number;
  "rebuilding": boolean;
};

export type RecallRequest = {
  "detail_level"?: "auto" | "summary" | "full";
  "query": string;
  "intent": "continue_conversation" | "exact_quote" | "current_fact" | "episode" | "timeline" | "open_commitment" | "procedure" | "cue_recovery" | "explore";
  "cue_event_ids": (Identifier)[];
  "additional_cues": (string)[];
  "valid_time_filter"?: TimeRange;
  "world_filter"?: ("real_world" | "quoted" | "roleplay" | "hypothetical" | "dream" | "unknown")[];
  "previous_recall_id"?: Identifier;
  "read_fence": ReadFence;
  "budget": Budget;
  "evidence_mode"?: "contextual" | "prior_record_check";
  "evidence_cutoff"?: EvidenceCutoff;
  "query_event_ids"?: (Identifier)[];
};

export type RankSignal = {
  "method": "keyword" | "vector" | "entity" | "temporal" | "association" | "recency" | "reranker";
  "score": number;
  "meaning": "ranking_only_not_probability";
  "model_or_index_version": Identifier;
};

export type MemoryVersion = { memory_id: Identifier; revision: number };
export type BeliefRelationRef = { relation_id: Identifier; revision: number; content_sha256: string };
export type BeliefRelation = {
  relation_id: Identifier;
  revision: number;
  scope: BoundScope;
  kind: "abstracts" | "equivalent" | "exception" | "supersedes" | "contradicts";
  source_memory_version: MemoryVersion;
  target_memory_versions: MemoryVersion[];
  evidence_refs: EvidenceRef[];
  conditions: SemanticCondition;
  valid_during: ValidDuring;
  world: MemoryAssertion["world"];
  condition_registry_fingerprint: string;
  verification: NonNullable<MemoryAssertion["verification"]>;
  root_event_ids: Identifier[];
  endpoint_snapshots: { memory_version: MemoryVersion; content_sha256: string }[];
};
export type BeliefRelationAssessment = { relation: BeliefRelation; applicability: MemoryApplicability };

export type BeliefResolution = {
  state: "unchanged" | "superseded" | "exception_applies" | "conflicted" | "uncertain";
  complete: boolean;
  relations?: BeliefRelationAssessment[];
  relation_refs?: BeliefRelationRef[];
  evidence_refs?: EvidenceRef[];
};

export type RecallItem = {
  "source_act"?: {
    act: "direct_statement" | "inquiry" | "instruction" | "correction" | "confirmation" | "presupposition" | "unknown" | "not_applicable";
    rule_version: "literal-episodes-v3";
  };
  "deferred_by"?: { memory_id: string; revision: number };
  "presentation"?: {
    level: "summary" | "full";
    reason: "active_detail" | "explicit_detail" | "protected_detail" | "detail_cue" | "requested_summary" | "background_gist";
    policy_version: "cue-sensitive-resolution-v1";
  };
  "memory": MemoryAssertion;
  "applicability"?: MemoryApplicability;
  "belief_resolution"?: BeliefResolution;
  "rank_signals": (RankSignal)[];
  "retrieved_from": "index" | "unindexed_tail" | "archive_scan";
  "display_mode": "explicit_recollection" | "background_context" | "do_not_surface";
};

export type SourcePassage = {
  policy_version: "bounded-source-context-v1" | "bounded-source-context-v2";
  evidence_ref: EvidenceRef;
  actor_role: "user" | "operator";
  delivery_state: "received";
  world: MemoryAssertion["world"];
  text: string;
  recorded_at: string;
  anchor_memory_versions: MemoryVersion[];
};

export type RecallResult = {
  "source_passages"?: SourcePassage[];
  "model_context"?: string;
  "context_format"?: "compact-memory-v1";
  "belief_relations"?: BeliefRelationAssessment[];
  "recall_id": Identifier;
  "scope": BoundScope;
  "status": "grounded" | "partial" | "ambiguous" | "conflicted" | "no_match" | "indexing" | "degraded" | "deleted";
  "items": (RecallItem)[];
  "unknowns": (string)[];
  "ambiguities": ({
  "question": string;
  "candidate_memory_ids": (Identifier)[];
})[];
  "conflicts": ({
  "description": string;
  "memory_ids": (Identifier)[];
})[];
  "coverage": Coverage;
  "usage": Usage;
  "next_step": "answer_with_evidence" | "answer_with_limits" | "request_cue" | "expand_search" | "retry_after_indexing" | "retry_service" | "do_not_reconstruct_deleted";
  "retrieval_snapshot": RetrievalSnapshot;
  "recall_traces": (RecallTrace)[];
};

export type ContextBlock = {
  "section": "working_state" | "relevant_memory" | "conditional_memory" | "verbatim_evidence" | "uncertainty" | "open_loops" | "integration_status" | "recent_context" | "current_situation";
  "text": string;
  "evidence_refs": (EvidenceRef)[];
  "trust": "memory_data_not_instructions";
  "token_count": number;
};

export type ContextPacket = {
  "orientation_assessment"?: OrientationAssessment;
  "model_context"?: string;
  "packet_id": Identifier;
  "turn_id": Identifier;
  "scope": BoundScope;
  "working_state": WorkingState;
  "blocks": (ContextBlock)[];
  "recall_results": (RecallResult)[];
  "coverage": Coverage;
  "budget": Budget;
  "usage": Usage;
  "integration": IntegrationCapabilities;
  "created_at": string;
  "retrieval_snapshot": RetrievalSnapshot;
  "recall_traces": (RecallTrace)[];
};

export type IntegrationCapabilities = {
  "adapter_id": Identifier;
  "adapter_version": Identifier;
  "protocol_version": "1.0.0-draft";
  "transport": "mcp_stdio" | "mcp_streamable_http" | "rest" | "sdk" | "api_proxy" | "host_plugin";
  "supported_actions": ("observe" | "prepare" | "recall" | "inspect" | "commit" | "checkpoint" | "correct" | "forget" | "export" | "restore")[];
  "capture": {
  "user_messages": boolean;
  "delivered_assistant_messages": boolean;
  "tool_results": boolean;
  "message_edits": boolean;
  "message_deletions": boolean;
  "stable_source_ids": boolean;
  "branch_identity": boolean;
};
  "hooks": {
  "before_turn": boolean;
  "after_delivery": boolean;
  "before_compaction": boolean;
  "on_resume": boolean;
};
  "context_control": "none" | "tool_output_only" | "pre_prompt" | "inference_loop";
  "active_recall": boolean;
  "automatic_capture": boolean;
  "automatic_recall": boolean;
  "guarantee_level": "tools_only" | "partial_automation" | "full_lifecycle";
  "verification_state": "declared" | "verified" | "unavailable";
  "verified_at": string | null;
  "limitations": (string)[];
};

export type AssertionProposal = {
  "statement": string;
  "memory_kind": "episodic" | "semantic" | "prospective" | "procedural" | "interaction_preference" | "relationship" | "self_model";
  "world_hint": "real_world" | "quoted" | "roleplay" | "hypothetical" | "dream" | "unknown";
  "evidence_refs": (EvidenceRef)[];
  "proposal_reason": string;
};

export type ObservationReceipt = {
  "accepted_event_ids": (Identifier)[];
  "duplicate_event_ids": (Identifier)[];
  "durable_through_sequence": number;
  "indexed_through_sequence": number;
  "consolidation_job_ids": (Identifier)[];
  "scope_generation": number;
};

export type CheckpointReceipt = {
  "checkpoint_id": Identifier;
  "working_revision": number;
  "coverage": Coverage;
  "restore_strategy": "indexed_state" | "state_plus_unindexed_tail" | "not_ready";
  "compaction_safe": boolean;
  "created_at": string;
};

export type JobReceipt = {
  "job_id": Identifier;
  "state": "queued" | "running" | "completed" | "failed";
  "revision": number;
  "created_at": string;
  "details": (string)[];
};

export type DeletionReceipt = {
  "deletion_id": Identifier;
  "mode": "suppress_recall" | "erase_content";
  "scope_generation": number;
  "recall_blocked_at": string;
  "purge_state": "not_requested" | "queued" | "running" | "completed";
  "backup_expiry_deadline": string | null;
  "tombstone_recorded": boolean;
  "affected_object_count": number;
};

export type PortableArchive = {
  "archive_id": Identifier;
  "format": "agent-memory-jsonl-bundle";
  "format_version": "1.0.0-draft";
  "manifest_sha256": string;
  "status": "preparing" | "ready" | "failed";
  "download_ref": string | null;
  "expires_at": string | null;
  "includes": ("events" | "assertions" | "working_states" | "relations" | "policies" | "tombstones" | "provenance" | "model_versions")[];
  "job": JobReceipt;
};

export type RuntimeError = {
  "code": "unauthorized" | "not_found_or_forbidden" | "invalid_evidence" | "identity_unverified" | "version_conflict" | "idempotency_conflict" | "budget_exceeded" | "dependency_unavailable" | "unsupported_capability" | "invalid_archive" | "deletion_in_progress" | "validation_failed" | "context_stale" | "source_unavailable" | "lease_conflict";
  "message": string;
  "retryable": boolean;
  "retry_after_ms"?: number;
  "current_revision"?: number;
};

export type ObservePayload = {
  "idempotency_key": Identifier;
  "events": (ObservedEventInput)[];
};

export type PrepareRecallSelection = {
  "recall_plan": RecallPlan;
  "evidence_mode": "prior_record_check";
  "expected_dependency_generation": number;
} | {
  "recall_plan"?: never;
  "evidence_mode"?: "contextual" | "prior_record_check";
  "expected_dependency_generation"?: number;
};

export type PreparePayload = {
  "assess_orientation"?: boolean;
  "turn_id": Identifier;
  "latest_input_event_ids": (Identifier)[];
  "read_fence": ReadFence;
  "budget": Budget;
  "capabilities": IntegrationCapabilities;
  "evidence_cutoff"?: EvidenceCutoff;
  "recent_event_limit"?: number;
} & PrepareRecallSelection;

export type RecallPayload = RecallRequest;

export type InspectPayload = {
  "memory_ids": (Identifier)[];
  "evidence_refs": (EvidenceRef)[];
  "include_successors": boolean;
  "include_corrections": boolean;
  "budget": Budget;
};

export type CommitPayload = {
  "turn_id": Identifier;
  "idempotency_key": Identifier;
  "expected_working_revision": number;
  "delivered_event_ids": (Identifier)[];
  "proposed_items": (WorkingItem)[];
  "proposed_open_loops": (OpenLoop)[];
  "assertion_proposals": (AssertionProposal)[];
  "recall_feedback"?: (RecallFeedbackInput)[];
};

export type CheckpointPayload = {
  "expected_working_revision": number;
  "through_sequence": number;
  "reason": "compaction" | "shutdown" | "migration" | "manual";
};

export type CorrectPayload = {
  "memory_id": Identifier;
  "expected_revision": number;
  "correction_event_ids": (Identifier)[];
  "replacement"?: AssertionProposal;
  "operation": "replace" | "retract" | "clarify";
  "idempotency_key": Identifier;
  "expected_dependency_generation": number;
};

export type ForgetPayload = {
  "idempotency_key": Identifier;
  "selector": {
  "kind": "events" | "memories" | "conversation" | "all_authorized_scope";
  "ids": (Identifier)[];
};
  "mode": "suppress_recall" | "erase_content";
  "expected_scope_generation": number;
};

export type ExportPayload = {
  "include_events": boolean;
  "include_working_states": boolean;
  "include_assertions": boolean;
  "include_provenance": true;
  "include_tombstones": true;
  "budget": Budget;
};

export type RestorePayload = {
  "uploaded_archive_ref": Identifier;
  "expected_manifest_sha256": string;
  "mode": "new_brain" | "merge_authorized_brain";
  "apply_current_tombstones": true;
  "expected_scope_generation": number;
  "budget": Budget;
};

export type ObserveResult = ObservationReceipt;

export type PrepareResult = ContextPacket;

export type InspectResult = {
  "memories": (MemoryAssertion)[];
  "events": (Event)[];
  "unavailable_refs": ({
  "requested_ref": Identifier;
  "reason": "not_found_or_forbidden" | "deleted" | "purge_pending" | "source_unavailable";
})[];
  "usage": Usage;
};

export type CommitResult = {
  "working_state": WorkingState;
  "accepted_proposal_count": number;
  "rejected_proposal_count": number;
  "consolidation_job_ids": (Identifier)[];
  "recall_traces": (RecallTrace)[];
};

export type CheckpointResult = CheckpointReceipt;

export type CorrectResult = {
  "updated_memory": MemoryAssertion;
  "invalidated_derivation_ids": (Identifier)[];
  "rebuild_job": JobReceipt;
  "transaction": CorrectionCommit;
};

export type ForgetResult = DeletionReceipt;

export type ExportResult = PortableArchive;

export type RestoreResult = {
  "job": JobReceipt;
  "scope_generation": number;
  "imported_event_count": number;
  "quarantined_event_count": number;
  "excluded_deleted_count": number;
  "read_enabled": boolean;
};

export type UtteranceSegment = {
  "act": "direct_statement" | "inquiry" | "presupposition" | "correction" | "confirmation" | "instruction" | "unknown" | "not_applicable";
  "span"?: {
  "start": number;
  "end": number;
  "unit": "unicode_code_point";
};
};

export type EvidenceCutoff = {
  "mode": "before_event" | "through_sequence";
  "event_id"?: Identifier;
  "through_sequence"?: number;
};

export type RetrievalSnapshot = {
  "snapshot_id": Identifier;
  "created_at": string;
  "observed_through_sequence": number;
  "dependency_generation": number;
  "scope_generation": number;
  "query_event_ids": (Identifier)[];
  "historical_evidence_status": "not_requested" | "enforced" | "unavailable";
  "evidence_cutoff": (EvidenceCutoff) | (null);
  "resolved_evidence_through_sequence": number | null;
  "excluded_cue_event_ids": (Identifier)[];
  "exclude_cue_descendants": boolean;
};

export type SupportPath = {
  "path_id": Identifier;
  "evidence_refs": (EvidenceRef)[];
  "source_memory_versions": ({
  "memory_id": Identifier;
  "revision": number;
})[];
  "permitted_scope": BoundScope;
  "dependency_generation": number;
  "state": "current" | "invalidated";
  "independently_supports_complete_statement": true;
};

export type DisclosureBasis = {
  "dependency_generation": number;
  "validation_state": "current" | "invalidated" | "no_support";
  "support_paths": (SupportPath)[];
  "selected_path_ids": (Identifier)[];
  "release_mode": "supported_scope" | "independent_shareable_version" | "no_evidence";
};

export type CorrectionCommit = {
  "correction_id": Identifier;
  "committed_at": string;
  "correction_event_ids": (Identifier)[];
  "durable_through_sequence": number;
  "previous_dependency_generation": number;
  "dependency_generation": number;
  "stale_projection_reads_blocked": true;
};

export type RecallTrace = {
  "trace_id": Identifier;
  "recall_id": Identifier;
  "retrieval_snapshot_id": Identifier;
  "scope": BoundScope;
  "turn_id": string | null;
  "memory_id": Identifier;
  "memory_revision"?: number;
  "kind": "candidate_hit" | "context_selected" | "quoted" | "background_influence" | "user_reintroduced" | "user_confirmed" | "user_corrected" | "observed_outcome";
  "source": "server_instrumentation" | "model_estimate" | "user_message" | "tool_observation";
  "feedback_event_id": string | null;
  "evidence_refs": (EvidenceRef)[];
  "recorded_at": string;
  "allowed_effect": "none" | "activation_only" | "external_reassessment";
};

export type RecallFeedbackInput = {
  "recall_id": Identifier;
  "memory_id": Identifier;
  "kind": "candidate_hit" | "context_selected" | "quoted" | "background_influence" | "user_reintroduced" | "user_confirmed" | "user_corrected" | "observed_outcome";
  "feedback_event_id": string | null;
  "evidence_refs": (EvidenceRef)[];
};

export type RecallPlan = {
  "belief_relation_refs"?: BeliefRelationRef[];
  "version": "evidence-recall-v1";
  "memory_versions": {
    "memory_id": Identifier;
    "revision": number;
  }[];
  "recent_event_refs": {
    "event_id": Identifier;
    "revision": number;
  }[];
  "observed_event_refs": {
    "event_id": Identifier;
    "revision": number;
  }[];
  "outcome": "sufficient" | "ambiguous" | "insufficient" | "not_needed" | "budget_exhausted" | "reasoner_unavailable" | "invalid_decision";
  "model_calls": number;
  "search_calls": number;
  "input_bytes": number;
  "elapsed_ms": number;
  "coverage_limitations": Coverage["limitations"];
  "trace": {
    "step": number;
    "action": "search" | "finish" | "stop";
    "reason": RecallPlan["outcome"] | "continue";
    "query_hashes": string[];
    "candidate_count": number;
    "selected_count": number;
  }[];
};

export type ActionPayloads = {
  observe: ObservePayload;
  prepare: PreparePayload;
  recall: RecallPayload;
  inspect: InspectPayload;
  commit: CommitPayload;
  checkpoint: CheckpointPayload;
  correct: CorrectPayload;
  forget: ForgetPayload;
  export: ExportPayload;
  restore: RestorePayload;
};

export type ActionResults = {
  observe: ObserveResult;
  prepare: PrepareResult;
  recall: RecallResult;
  inspect: InspectResult;
  commit: CommitResult;
  checkpoint: CheckpointResult;
  correct: CorrectResult;
  forget: ForgetResult;
  export: ExportResult;
  restore: RestoreResult;
};

export type Action = keyof ActionPayloads;
export type ActionRequest<A extends Action = Action> = {
  message_type: "request";
  contract_version: "1.0.0-draft";
  request_id: string;
  action: A;
  target: ScopeSelector;
  payload: ActionPayloads[A];
};
export type ActionResponse<A extends Action = Action> = {
  message_type: "response";
  contract_version: "1.0.0-draft";
  request_id: string;
  action: A;
} & ({
  outcome: "accepted" | "completed" | "degraded";
  server_scope: BoundScope;
  result: ActionResults[A];
} | {
  outcome: "conflict" | "rejected" | "failed";
  error: RuntimeError;
});
