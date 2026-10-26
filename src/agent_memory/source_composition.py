from __future__ import annotations

SOURCE_COMPOSITION_INPUT_VERSION = "source-composition-v1"
PARTICIPANT_ACTIONS = (
    "Preserve the actor of each described action, separately from who reported the account. "
    "For each predicate, check who acted, what they did, to whom or what, and with which qualifiers. "
    "Do not attach several people's actions to one named subject, infer that an observer participated, "
    "or resolve an ambiguous pronoun by plausibility. A collective activity does not establish that "
    "every named participant performed every action. Split clauses with explicit actors, or describe "
    "the shared topic without assigning unsupported actions."
)
COMPOSITION_INSTRUCTIONS = (
    "Source composition procedure: before writing a factual description, identify the supported "
    "event clauses and keep their participants, actions, objects, time, frequency, uncertainty and "
    "expressed meaning fixed. " + PARTICIPANT_ACTIONS + " "
    "For text in the user's voice, build the remembered scene from those clauses first; then change "
    "wording and sentence rhythm only. A natural-sounding modifier can assert a new fact: check "
    "adjectives, possessives, pluralization and habitual phrasing as carefully as main verbs. Do "
    "not infer circumstances from a setting or transfer a detail from one occasion to others. "
    "Read the whole draft once more against the source, "
    "including decorative phrases and the final sentence. Remove unsupported factual additions "
    "rather than qualifying them in an earlier disclaimer. If faithful paraphrase is uncertain, "
    "quote the relevant source wording and write around it. Do not expose this checking procedure "
    "or a factual checklist in ordinary conversation."
)
PARTICIPANT_VERIFY_INSTRUCTIONS = (
    "For episode_actions only, also return organization_fidelity.participant_actions. This is distinct from speaker "
    "attribution: check the people and objects INSIDE the reported scene. Evaluate every predicate "
    "and modifier in the organization_statement, including compressed titles and coordinated "
    "phrases. " + PARTICIPANT_ACTIONS + " "
    "If the wording admits an unsupported actor-action assignment under its ordinary reading, "
    "return unknown even if a more charitable reading would match the source. All five fidelity "
    "facets are required. A positive whole-proposal or attribution verdict cannot override this "
    "check. Do not rewrite the proposed statement."
)
