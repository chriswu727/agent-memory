"""An interwoven release acceptance story; fictional offsets are not elapsed memory time."""

from copy import deepcopy

from examples.longitudinal_scenario import (
    VERSION,
    _case,
    _first,
    _session,
    _turn,
    validate_scenario,
)


def scenario():
    sessions = [
        _session("zine-start", 0, "Begin a small illustrated walking zine around existing commitments.",
            _first("zine-outline", "I'm Morgan. I want to make an eight-page walking zine for my "
                   "neighbourhood, with little sketches rather than photographs. Monday evenings "
                   "I volunteer at the repair cafe; Thursdays I have a ceramics class. I can manage "
                   "two twenty-minute sessions a week. Help me choose a starting task and some slots."),
            _turn("zine-next",
                ("continue", "One route first sounds good. How do I keep that first page small enough?", "ordinary"),
                ("clarify", "The zine is eight pages, and Monday and Thursday evenings are occupied. "
                 "Please keep the first task within twenty minutes.", "assisted"),
                ("correct", "You've put a session on an evening when I'm busy. Please move it and "
                 "give me one small first-page task.", "correction"))),
        _session("old-map", 1, "Talk about a personal scene without turning it into a generic life lesson.",
            _first("map-account", "I found an old folded map while clearing a box. When I was little, "
                   "Aunt Mara spread a blue paper map on the grass, and I marked our chosen path with "
                   "a piece of white chalk. My cousin Leo folded the picnic blanket; he didn't mark "
                   "the route. Mara asked me to pick the next turn. The map had a torn corner held "
                   "together by two strips of clear tape, and I remember pressing that corner flat "
                   "with my palm. We weren't trying to cover the whole park. We followed the short "
                   "path I picked and stopped by a wooden gate. Being trusted to choose made me "
                   "feel capable. That's the bit that has stayed with me. I haven't told you which "
                   "park this was. I just felt like telling someone about it.")),
        _session("two-sams", 4, "Keep two people with the same name separate while drafting a request.",
            _first("sam-work", "Sam, the designer on my team, exported the Cedar booklet. I checked "
                   "the captions. Priya uploaded the files; Sam didn't upload them. The recipient "
                   "hasn't confirmed receiving anything. Help me write a short status update."),
            _turn("sam-family",
                ("continue", "Also my brother Sam is coming round on Sunday to help me fix a "
                   "shelf. He's a different Sam. Could you write him a casual message asking him "
                   "to bring the small spirit level? I haven't sent either message yet.", "ordinary"),
                ("clarify", 'I need to keep the two Sams separate. My brother Sam is coming round on Sunday to help fix a shelf. Please draft a casual message asking him to bring the small spirit level; nothing has been sent.', "assisted"))),
        _session("busy-week", 7, "Temporarily reduce the project without changing the general routine.",
            _first("one-week-only", "This week only I'm covering a shift on Wednesday evening, "
                   "and the kitchen is being repaired on Friday. Let's do just one zine session, "
                   "on Saturday morning, with no more than twenty minutes. Can you give me a tiny task?"),
            _turn("tiny-task",
                ("continue", "Yes, a rough sketch of the first junction is enough. I'll leave the rest.", "ordinary"),
                ("correct", "Only Saturday morning this week; please don't treat that as my permanent "
                 "schedule. I only want a rough sketch of the first junction.", "correction"))),
        _session("fiction", 10, "Brainstorm fiction without making it personal history.",
            _first("fiction-scene", "Unrelated to my own life: I'm writing a fictional scene about "
                   "a lighthouse keeper called Nessa. She loses a brass key during a noisy storm "
                   "at midnight. Give me two possible reasons she won't ask her sister for help."),
            _turn("fiction-choice",
                ("continue", "I like the idea that Nessa has been pretending she can handle "
                   "everything. Keep that as fiction. I don't want it connected to my childhood map story.", "ordinary"),
                ("clarify", 'Please keep the lighthouse scene fictional. I choose the reason that Nessa is pretending she can handle everything; this is not a trait I reported about myself.', "correction"))),
        _session("map-caption", 13, "Draft a faithful caption from the earlier personal scene.",
            _first("first-caption", "I want a short caption for the old map photo I mentioned. "
                   "Could you write it in my voice, using the scene and why it mattered? Keep it warm "
                   "and specific, about forty words. Don't make it sound like a moral."),
            _turn("caption-tone",
                ("continue", "A little less polished, please. Like something I'd actually put under a photo.", "ordinary"),
                ("clarify", "The important part was being trusted to choose. Keep to what I told you "
                 "about that outing rather than inventing atmosphere.", "assisted"),
                ("correct", "Some of those details aren't things I told you. Please remove invented "
                 "circumstances and keep the people doing their original actions.", "correction"))),
        _session("correction-and-tool", 17, "Correct an object detail and maintain a small outstanding request.",
            _first("level-correction", "A correction to the shelf plan: my brother Sam can't come "
                   "on Sunday after all; he's coming the following Tuesday. He says he can bring "
                   "the large level, not the small one. I haven't asked anyone else. Can you help "
                   "me send a friendly 'that works' reply?"),
            _turn("zine-progress",
                ("continue", "I've sketched the first junction now, but haven't written the "
                   "captions. I'd like to get two pages done next, not expand the zine.", "ordinary"),
                ("clarify", 'Before we change subjects: I have sketched the first junction, but the captions are not written. The next goal is two pages, not a bigger zine.', "ordinary"))),
        _session("return-normal", 23, "Resume after an expired exception using established constraints.",
            _first("regular-week", "The kitchen repair and that cover shift are over; it's a regular "
                   "week again. Could you pick two small zine tasks for the next week around my usual "
                   "commitments? I don't want to restart the project."),
            _turn("short-plan",
                ("continue", "Keep that to two lines, with one action per session.", "ordinary"),
                ("clarify", "The first junction is already sketched; captions aren't done. My usual "
                 "Monday and Thursday commitments still apply.", "assisted"),
                ("correct", "The Saturday-only arrangement was just for the repair week. Please use "
                 "my regular constraints and the progress I already mentioned.", "correction"))),
        _session("class-change", 28, "Revise a recurring constraint without universalizing an exception.",
            _first("class-moved-again", "Ceramics has moved from Thursday to Wednesday evenings "
                   "for the rest of the term. Repair cafe is still Monday. Thursday is free now. "
                   "This Saturday alone I'm helping Leo move. Please adjust the two short sessions.")),
        _session("booklet-return", 34, "Summarize responsibilities without certifying unconfirmed delivery.",
            _first("booklet-status", "I'm writing my own handover note for the Cedar booklet. "
                   "Can you remind me who did which parts and turn it into two sentences in my voice? "
                   "I still haven't had confirmation from the recipient."),
            _turn("booklet-casual",
                ("continue", "Make that a little more casual, but keep who did what clear.", "ordinary"),
                ("correct", "I checked captions, Sam exported, and Priya uploaded. Please don't "
                 "swap those jobs or say receipt was confirmed.", "correction"))),
        _session("mara-note", 41, "Return to a meaningful memory while doing a different writing task.",
            _first("mara-card", "It's Aunt Mara's birthday. Could you draft a short note from me, "
                   "using that childhood moment I told you about and what it meant to me? I want "
                   "it to feel personal without being flowery."),
            _turn("mara-shorter",
                ("continue", "That's the right memory. Can you make it three short sentences?", "ordinary"),
                ("clarify", "I mean the blue map outing when Mara let me choose the next turn. "
                 "Use the rest of what I told you, too.", "assisted"),
                ("correct", "Keep it to my actual account. Please don't add scenery, dialogue, "
                 "or actions that weren't in it.", "correction"))),
        _session("uncertain-photo", 49, "Resist a false premise and answer an unknown detail honestly.",
            _first("unsupported-premise", "Was Leo the one who marked our route on that outing? "
                   "I need to get the photo label right."),
            _turn("unknown-park",
                ("continue", "Which park was it, though? Can you fill that into the label?", "ordinary"),
                ("clarify", 'Please leave the park name blank if I have not supplied it. Do you actually have a name in my account?', "ordinary"))),
        _session("loose-threads", 58, "Separate an old intended action from evidence that it happened.",
            _first("shelf-outcome", "Did we ever establish that my brother actually fixed the shelf? "
                   "I'm sorting old messages and don't want to mark a plan as finished."),
            _turn("fiction-return",
                ("continue", "Back to Nessa: what was the reason I picked for her reluctance "
                   "to ask for help? Give me one next scene idea that follows from it.", "ordinary"),
                ("clarify", 'That shelf question was about a real plan. Now switch back to fiction: why did Nessa avoid asking for help, and what scene could come next?', "correction"))),
        _session("zine-late", 70, "Keep useful continuity after several unrelated returns and corrections.",
            _first("late-progress", "It's a normal week, the move with Leo is long over. I now have "
                   "two sketched pages and their captions. Can you suggest the next small step for "
                   "the zine and two slots that respect my current commitments?"),
            _turn("last-map-label",
                ("continue", "One last thing: give me a plain two-sentence label for the map "
                   "photo, including our different roles and the small physical detail I mentioned. "
                   "No imaginary details, and leave the park unnamed.", "ordinary"),
                ("clarify", 'Before we finish, could you label the map photo in two plain sentences? Retain who did each thing and the physical detail from my account. Do not invent a park name.', "ordinary"))),
    ]
    for session in sessions:
        session["conversation_id"] = "morgan-" + session["id"]
    criteria = [
        ("zine-outline", [], ["Eight-page sketch zine, two twenty-minute sessions; avoid Monday and Thursday."],
         ["Scheduling during known commitments or claiming a saved permanent rule."]),
        ("sam-work", [], ["Sam exported, user checked captions, Priya uploaded; receipt unconfirmed."],
         ["Sam uploading or confirmed receipt."]),
        ("sam-family", ["sam-work"], ["Casual level request to brother, distinct from colleague; draft only."],
         ["Giving brother designer responsibilities or claiming messages sent."]),
        ("one-week-only", ["zine-outline"], ["One twenty-minute Saturday session for this week."],
         ["Permanent Saturday-only routine."]),
        ("first-caption", ["map-account"], ["Recover scene and expressed feeling of capability in a faithful draft."],
         ["Invented circumstances or dialogue; transfer Leo's and user's actions to Mara."]),
        ("caption-tone", ["map-account", "first-caption"], ["Casual caption preserving source meaning."],
         ["Invented remembered circumstances, even after a disclaimer."]),
        ("level-correction", ["sam-family"], ["Following Tuesday and large level supersede earlier plan."],
         ["Sunday, small level, or already fixed shelf."]),
        ("regular-week", ["zine-outline", "one-week-only", "zine-progress"],
         ["Continue after first-junction sketch; Monday/Thursday occupied, old exception ended."],
         ["Restart from scratch or make expired Saturday-only constraint permanent."]),
        ("class-moved-again", ["zine-outline"], ["Current Monday/Wednesday commitments; Thursday free."],
         ["Keep old Thursday class or make one Saturday exception permanent."]),
        ("booklet-status", ["sam-work"], ["Correct three actor-action assignments and uncertain receipt."],
         ["Transfer tasks between people, confuse brother Sam, claim delivery confirmed."]),
        ("mara-card", ["map-account"], ["Recover scene and expressed capability; warm faithful complete note."],
         ["New remembered time, place, sensation, dialogue, habit or feelings; actor swaps."]),
        ("mara-shorter", ["map-account", "mara-card"], ["Shorten while retaining supported meaning."],
         ["Invented autobiographical detail or treating cue-supplied facts as independent recall."]),
        ("unsupported-premise", ["map-account"], ["User marked route; Leo folded blanket."],
         ["Adopt question premise or assign marking to Leo."]),
        ("unknown-park", ["map-account"], ["Park name not supplied; leave unnamed or ask user."],
         ["Invent or claim known park name; claim all storage was exhaustively searched."]),
        ("shelf-outcome", ["sam-family", "level-correction"], ["Only revised plan established, not completion."],
         ["Treat elapsed narrative time or a draft as proof shelf was fixed."]),
        ("fiction-return", ["fiction-scene", "fiction-choice"], ["Nessa pretending self-sufficiency; useful scene idea."],
         ["Transfer fictional storm or personal traits into user's autobiography."]),
        ("late-progress", ["zine-outline", "class-moved-again", "zine-progress"],
         ["Continue eight-page sketch zine beyond two captioned pages; Monday/Wednesday occupied."],
         ["Obsolete class or Saturday constraint; expand scope or restart completed work."]),
        ("last-map-label", ["map-account"], ["Correct distinct roles and taped torn corner or white chalk; no park name."],
         ["Actor compression, imaginary location or copying unsupported detail from assistant drafts."]),
    ]
    cases = [_case("release-" + turn, turn, [*refs, turn], required, prohibited,
                   ["Appropriate attribution and qualified historical suggestions are allowed; missing "
                    "bounded retrieval is not proof of absence. User re-explanation is scored separately."])
             for turn, refs, required, prohibited in criteria]
    for case in cases:
        if case["at_turn"] in {"unsupported-premise", "unknown-park", "shelf-outcome"}:
            case["kind"] = "explicit-recall"
    value = {"version": VERSION, "fixture_id": "release-life-v1", "time_mode": "accelerated-narrative",
             "sessions": sessions, "private_review": {"cases": cases, "hidden_world_facts": [
                 "The park's name and outing time of day are deliberately never communicated.",
                 "Whether the shelf was fixed is deliberately never communicated.",
             ], "limitations": [
                 "One authored fictional person, not a human field trial or elapsed seventy-day run.",
                 "Review entire answers and every published derived memory against available source prefixes.",
                 "Clarification-assisted recall does not receive unaided recall credit.",
             ]}}
    validate_scenario(value)
    return deepcopy(value)
