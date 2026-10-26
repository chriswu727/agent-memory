"""A frozen fictional life, exact user branches, and a separate review ledger."""

from __future__ import annotations

from copy import deepcopy

VERSION = "longitudinal-life-v1"
BRANCHES = frozenset({"ordinary", "assisted", "correction"})


def _turn(turn_id, *choices):
    return {"id": turn_id, "choices": [
        {"id": choice_id, "text": text, "branch": branch}
        for choice_id, text, branch in choices
    ]}


def _first(turn_id, text):
    return _turn(turn_id, ("start", text, "ordinary"))


def _session(session_id, day, intent, *turns):
    return {"id": session_id, "day_offset": day,
            "conversation_id": f"rowan-{session_id}", "intent": intent, "turns": list(turns)}


def _case(case_id, turn_id, sources, required, prohibited, uncertainty, **extra):
    return {"id": case_id, "at_turn": turn_id, "kind": "practical",
            "source_refs": [{"turn_id": source} for source in sources],
            "required": required, "prohibited": prohibited,
            "acceptable_uncertainty": uncertainty, **extra}


def scenario():
    sessions = [
        _session("starting-the-story", 0, "Make a small, realistic writing plan.",
            _first("story-start", "I'd like to finish a short story, about 1,800 words. "
                   "Lea is the night caretaker at an observatory and keeps putting off opening "
                   "a letter from her sister. I've only got a rough outline. My evening class "
                   "takes up Tuesdays and Thursdays. Can we find two short writing slots?"),
            _turn("story-slots",
                ("continue", "Half an hour at a time sounds manageable. What should I try "
                 "to get onto the page first?", "ordinary"),
                ("clarify", "Outside those two class nights I'm flexible. Keep each session "
                 "to half an hour. What should I start with?", "assisted"),
                ("correct", "Tuesday and Thursday evenings won't work because of class. "
                 "Could you move those slots and give me one small starting task?", "correction"))),
        _session("work-handoff", 2, "Draft a concise request to a colleague.",
            _first("work-alex", "Could you help me write a message to Alex, the analyst on my "
                   "team? I need him to check the access list in the Juniper folder before "
                   "I send the handoff. I don't want it to sound like I'm chasing him."),
            _turn("work-tone",
                ("shorter", "Make it a bit shorter, please. We talk pretty casually.", "ordinary"),
                ("clarify", "Just a chat message to my colleague, not a formal email. "
                 "I haven't sent the handoff yet.", "assisted"))),
        _session("weekend-invitation", 5, "Arrange an easy outing with a friend.",
            _first("friend-alex", "My friend Alex, the drummer, and I are thinking of walking "
                   "by the river tomorrow. He's a different Alex from the one at work. "
                   "Help me suggest a relaxed afternoon without making a whole itinerary."),
            _turn("walk-plan",
                ("simple", "A walk and a stop for tea is enough. Can you turn that into "
                 "a casual text?", "ordinary"),
                ("correct", "This is my friend who plays drums, not my colleague. "
                 "Just suggest a walk and tea.", "correction"))),
        _session("schedule-change", 8, "Adjust the writing routine after a timetable change.",
            _first("class-moved", "The class has moved to Wednesday evenings from now on. "
                   "Tuesday and Thursday are free again. This Friday alone I've got the "
                   "repair person coming round. Could you adjust the writing plan?"),
            _turn("opening-stuck",
                ("continue", "I'm stuck on the opening. Could we start with Lea "
                 "doing something ordinary instead of explaining her whole past?", "ordinary"),
                ("clarify", "The observatory story, with Lea and the unopened letter. "
                 "I need help starting the opening, not a new story idea.", "assisted"))),
        _session("a-cancelled-walk-and-a-photo", 11, "Check in on a friend, then caption a family photo.",
            _first("walk-cancelled", "That Sunday river walk with Alex never happened. "
                   "He twisted his ankle and we cancelled. Could you help me check in "
                   "without making him feel he has to arrange another outing?"),
            _turn("blackout-memory",
                ("change-topic", "Thanks. On a different note, I found an old photo. "
                 "When our power went out, Grandma would light a lantern and put buttons "
                 "in a tea tin for us to sort. It made a scary dark house feel safe. "
                 "Could you help me write a quiet caption?", "ordinary"),
                ("change-after-correction", "I'd leave out any mention of how nice the walk "
                 "was, since we cancelled. Different subject: I found an old photo. "
                 "During power cuts Grandma lit a lantern and let us sort buttons "
                 "in a tea tin. It made the dark house feel safe. Could you write "
                 "a quiet caption?", "correction")),
            _turn("caption-tone",
                ("plain", "Less sentimental, please. It was a small ordinary kindness.", "ordinary"),
                ("clarify", "The feeling of being safe is what matters to me, "
                 "rather than a dramatic description of the blackout.", "assisted"))),
        _session("return-to-writing", 14, "Resume the unfinished opening and plan the week.",
            _first("story-return", "It's the following Monday now, and I've got some room "
                   "this week. Can we get back to "
                   "the story and find two evenings to work on it? I haven't written "
                   "the opening yet."),
            _turn("story-return-response",
                ("continue", "Let's make the first session very small. "
                 "Give me a couple of possible first sentences.", "ordinary"),
                ("clarify", "It's Lea at the observatory, still avoiding her sister's "
                 "letter. Wednesday is my class night now. Could we work from that?", "assisted"),
                ("correct", "Wednesday is the only class night now, and last Friday's "
                 "repair visit is over. Please adjust the plan and keep the story unfinished.", "correction"))),
        _session("correcting-the-handoff", 17, "Correct a work detail before the handoff is sent.",
            _first("folder-corrected", "I gave you the wrong folder name for that access "
                   "review. It's Harbor, not Juniper. The colleague is still Alex, "
                   "the analyst. Could you fix the message? I still haven't sent it."),
            _turn("handoff-final",
                ("continue", "That tone works. Could you give me just the final message "
                 "so I can copy it?", "ordinary"),
                ("correct", "Use Harbor and leave Juniper out of the message. "
                 "It's a correction, not a second folder to check.", "correction"))),
        _session("museum-sunday", 21, "Write a short journal note about a visit that actually happened.",
            _first("museum-visited", "Alex and I made it to the small design museum "
                   "yesterday. My drummer friend, I mean. His ankle was better, so we "
                   "went slowly and had a good afternoon. Could you help me put that "
                   "in my journal without making it sound like a travel review?"),
            _turn("visits-separate",
                ("continue", "Maybe one sentence about being glad we finally got "
                 "to spend some time together.", "ordinary"),
                ("correct", "The museum visit happened yesterday; the river walk "
                 "was the earlier plan we cancelled. Please keep those separate.", "correction"))),
        _session("dinner-with-nia", 25, "Plan a simple dinner for a guest.",
            _first("nia-dinner", "Nia's coming over on Sunday. She hates mushrooms, "
                   "though I eat them all the time. Could you suggest a simple "
                   "vegetarian dinner for us? I don't want to spend all afternoon cooking."),
            _turn("dinner-shopping",
                ("continue", "Could you turn that into a short shopping list for two?", "ordinary"),
                ("correct", "Please keep mushrooms out of this dinner for Nia. "
                 "A short shopping list for two would help.", "correction"))),
        _session("unfinished-is-unfinished", 28, "Make useful progress without treating a plan as an achievement.",
            _first("draft-still-open", "I didn't finish the story after all. I've got "
                   "about 250 words of the opening. Lea still hasn't opened the letter. "
                   "Could we figure out the next small step?"),
            _turn("next-scene",
                ("continue", "I like working on the moment just before she opens it. "
                 "Can you give me two ways to build that tension?", "ordinary"),
                ("correct", "It's only the opening so far, not a finished draft "
                 "to polish. Could we stay with the moment before the letter is opened?", "correction"))),
        _session("a-household-decision", 33, "Make a small practical decision unrelated to the ongoing story.",
            _first("lamp-choice", "I'm sorting out the desk. I've got a lamp that works "
                   "but takes up too much space, and buying another isn't in this month's "
                   "budget. Help me think of a couple of ways to rearrange things."),
            _turn("desk-followup",
                ("continue", "I'd rather move things around than buy storage boxes. "
                 "What would you try first?", "ordinary"),
                ("clarify", "There's a shelf next to the desk, but no wall mounting "
                 "allowed in the flat. What would you try first?", "assisted"))),
        _session("a-card-for-grandma", 37, "Find a personal starting point for a short card.",
            _first("grandma-card", "I'm writing Grandma a card and don't want a generic "
                   "thank-you. Is there something I've told you about her that "
                   "we could use as a starting point?"),
            _turn("grandma-cue",
                ("continue", "That small feeling of being safe is what I want to keep. "
                 "Could you make it a couple of plain sentences?", "ordinary"),
                ("hint", "I'm thinking of the power cuts and the tea tin. "
                 "Does that help? I'd like a couple of plain sentences.", "assisted"),
                ("correct", "I meant the power cuts, the lantern and sorting buttons "
                 "in the tea tin. Not a trip or a special present. "
                 "Could you write a couple of plain sentences about feeling safe?", "correction"))),
        _session("planning-after-a-gap", 42, "Return to the unfinished writing project after other activities.",
            _first("late-week-plan", "Two weeks have slipped by since I last worked on "
                   "the story. Let's make next week's plan. I'd like to get "
                   "the story moving again, but keep it realistic around class. "
                   "Where had we got to, and what should I work on next?"),
            _turn("late-plan-response",
                ("continue", "Two half-hour sessions is enough. Can you give each "
                 "one a small target?", "ordinary"),
                ("clarify", "It's still just about 250 words, with Lea avoiding "
                 "the letter. Wednesday is class night. Two half-hour sessions, please.", "assisted"),
                ("correct", "I haven't finished it, and the old Tuesday/Thursday "
                 "class timetable no longer applies. Can you redo the plan?", "correction"))),
        _session("dinner-for-one", 47, "Use what is on hand for a quick solo dinner.",
            _first("solo-mushrooms", "Just me for dinner tonight. I've got mushrooms, "
                   "rice and frozen peas. Can you suggest something easy with those?"),
            _turn("solo-method",
                ("continue", "Can you give me a short version of the method? "
                 "I'm tired and don't need anything fancy.", "ordinary"),
                ("correct", "Nia isn't coming tonight, and I like mushrooms. "
                 "Could you use what I've actually got?", "correction"))),
        _session("looking-for-the-photo", 51, "Look for a photo while distinguishing remembered facts from guesses.",
            _first("photo-location", "I'm looking for that old blackout photo. "
                   "Where did I say I'd put it? Help me work out where to start looking."),
            _turn("photo-search",
                ("continue", "Okay, I'll check the usual paper piles first. "
                 "A tiny search checklist would help.", "ordinary"),
                ("clarify", "I don't think I ever told you where I put it. "
                 "Let's treat places to look as suggestions.", "assisted"),
                ("correct", "I never told you a drawer or a room for it. "
                 "Please don't present a guess as something I said.", "correction"))),
        _session("a-finished-draft", 56, "Share news of completing a draft without pretending it has been sent.",
            _first("draft-finished", "I finally finished the first draft, roughly "
                   "1,800 words. I haven't sent it to anyone yet. Could you help me "
                   "write a low-pressure text asking my drummer friend Alex "
                   "if he'd like to read it?"),
            _turn("sharing-text",
                ("continue", "Keep it friendly and make it easy for him to say "
                 "he's too busy. I'll send it myself later.", "ordinary"),
                ("correct", "This is my friend, not the analyst at work. "
                 "And I haven't sent either the draft or the invitation yet.", "correction"))),
    ]
    cases = [
        _case("initial-practical-plan", "story-start", ["story-start"],
              ["Offer two workable writing slots or a brief necessary scheduling clarification.",
               "Treat the story as an outline and respect Tuesday/Thursday evening class."],
              ["A completed draft", "A claimed external calendar booking"],
              ["Specific free times have not been supplied."]),
        _case("same-session-opening", "story-slots", ["story-start", "story-slots"],
              ["Give a small first writing task consistent with Lea, the observatory and the letter.",
               "Respect the selected current request and two class evenings."],
              ["Ask the user to repeat the story premise that is in current context."], []),
        _case("changed-schedule", "class-moved", ["story-start", "class-moved"],
              ["Use Wednesday as the recurring class evening; Tuesday/Thursday are free.",
               "Treat this Friday's repair visit as a one-off constraint."],
              ["Treat the repair visit as a permanent Friday commitment."], []),
        _case("cancelled-outing", "walk-cancelled", ["friend-alex", "walk-cancelled"],
              ["Draft a check-in to the drummer friend after the cancelled walk."],
              ["Claim the river walk occurred", "Assign the injury to the colleague"], []),
        _case("first-return-without-reteaching", "story-return",
              ["story-start", "class-moved", "story-return"],
              ["Resume the observatory story and offer two evenings excluding Wednesday.",
               "The opening is unfinished; the previous Friday exception has expired."],
              ["Ask for the whole story premise again", "Block Tuesday/Thursday as class nights",
               "Treat every Friday as unavailable"],
              ["May ask which of otherwise free evenings the user prefers."],
              temporal_scope="Reason over the user's explicit following-Monday statement, not memory decay."),
        _case("corrected-work-detail", "folder-corrected", ["work-alex", "folder-corrected"],
              ["Draft a request to colleague Alex to check Harbor; handoff remains unsent."],
              ["Request both Juniper and Harbor", "Send or claim to send a message",
               "Treat the drummer friend as the work colleague"], []),
        _case("separate-similar-episodes", "museum-visited",
              ["friend-alex", "walk-cancelled", "museum-visited"],
              ["Write about the completed design-museum visit with the drummer friend."],
              ["Blend the cancelled river walk into the completed visit",
               "Invent sights, exhibits or an earlier completed outing"],
              ["Need not mention the cancelled walk unless useful."]),
        _case("guest-preference-scope", "nia-dinner", ["nia-dinner"],
              ["Suggest a simple vegetarian meal without mushrooms for this visit."],
              ["Assert that the user dislikes mushrooms", "Invent a food allergy"], []),
        _case("plan-is-not-completion", "draft-still-open", ["story-start", "draft-still-open"],
              ["Help extend the approximately 250-word opening with the letter still unopened."],
              ["Treat an earlier plan or model suggestion as a completed draft"], []),
        _case("unrelated-task-focus", "lamp-choice", ["lamp-choice"],
              ["Offer rearrangement options using the working lamp without requiring purchases."],
              ["Insert the story, cancelled walk, family memory or class timetable gratuitously"],
              ["May ask about space dimensions; no shelf or mounting constraint is known yet."]),
        _case("delayed-meaningful-memory", "grandma-card", ["blackout-memory", "grandma-card"],
              ["Use the previously communicated blackout kindness as a personal card starting point.",
               "Distinguish remembering the emotional meaning from reproducing every incidental detail."],
              ["Invent a different shared event, a town, a date or Grandma's name"],
              ["Acknowledge uncertain incidental details rather than inventing them.",
               "Asking for a reminder is assistance, not unaided recall success."]),
        _case("cue-assisted-card", "grandma-cue", ["blackout-memory", "grandma-card", "grandma-cue"],
              ["Write a plain personal card consistent with the delivered cue and prior memory.",
               "Classify the outcome by the actual selected branch; earlier failure remains recorded."],
              ["Treat a cue-assisted answer as independent proof of unaided recall",
               "Claim this sequential interaction is an identical-state cue-ladder experiment"], []),
        _case("late-project-continuity", "late-week-plan",
              ["story-start", "class-moved", "story-return", "draft-still-open", "late-week-plan"],
              ["Resume the unfinished 250-word opening; respect Wednesday class.",
               "Propose practical next writing steps without requiring the established background again."],
              ["Declare the draft complete", "Revive Tuesday/Thursday class or the expired Friday visit"], [],
              temporal_scope="Use communicated elapsed context and schedule changes; real-time aging is unassessed."),
        _case("no-guest-preference-leak", "solo-mushrooms", ["nia-dinner", "solo-mushrooms"],
              ["Suggest a quick dinner using the user's available mushrooms, rice and peas."],
              ["Apply Nia's dislike to the user's solo meal", "Bring unrelated personal history into the recipe"], []),
        _case("uncommunicated-location", "photo-location", ["blackout-memory", "photo-location"],
              ["State that no storage location has been established and offer clearly labelled search suggestions."],
              ["Claim any drawer, room or storage place was previously communicated"],
              ["Uncertainty about the photo location is required; refusing all practical help is not."],
              kind="explicit-recall"),
        _case("new-completion-not-delivery", "draft-finished",
              ["friend-alex", "draft-still-open", "draft-finished"],
              ["Update the writing state to completed first draft and help invite the drummer friend to read it.",
               "The draft and invitation remain unsent."],
              ["Treat the friend as the analyst", "Claim the draft has been read or the message sent"], []),
    ]
    result = {"version": VERSION, "time_mode": "accelerated-narrative", "sessions": sessions,
              "private_review": {"cases": cases, "hidden_world_facts": [
                  "The photo is in the hallway drawer; Rowan never communicates this location.",
                  "Grandma's name and childhood town are deliberately never communicated.",
              ], "limitations": [
                  "One authored fictional person; this is not a human field trial.",
                  "Fifty-six narrative days do not establish real elapsed operation or memory aging.",
                  "The card follow-up measures ordinary assisted interaction, not an isolated cue ladder.",
                  "Process restart, offline intervals and model-callback changes require runner evidence.",
              ]}}
    validate_scenario(result)
    return deepcopy(result)


def _text(value, label, limit=16_384):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {label}")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError(f"Invalid {label}") from None


def validate_scenario(value):
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError("Unsupported scenario version")
    if value.get("time_mode") != "accelerated-narrative":
        raise ValueError("Unsupported scenario time mode")
    sessions = value.get("sessions")
    if not isinstance(sessions, list) or not 1 <= len(sessions) <= 32:
        raise ValueError("Scenario requires one to thirty-two sessions")
    session_ids, turns, positions = set(), {}, {}
    previous_day = -1
    for session in sessions:
        if not isinstance(session, dict):
            raise ValueError("Invalid session")
        for key in ("id", "conversation_id", "intent"):
            _text(session.get(key), f"session {key}")
        if session["id"] in session_ids:
            raise ValueError("Duplicate session id")
        session_ids.add(session["id"])
        day = session.get("day_offset")
        if type(day) is not int or not previous_day < day <= 365:
            raise ValueError("Session days must increase within one narrative year")
        previous_day = day
        if not isinstance(session.get("turns"), list) or not 1 <= len(session["turns"]) <= 8:
            raise ValueError("Session requires one to eight turns")
        for index, turn in enumerate(session["turns"]):
            if not isinstance(turn, dict):
                raise ValueError("Invalid turn")
            _text(turn.get("id"), "turn id")
            if turn["id"] in turns:
                raise ValueError("Duplicate turn id")
            positions[turn["id"]] = len(turns)
            turns[turn["id"]] = turn
            choices = turn.get("choices")
            if not isinstance(choices, list) or (
                len(choices) != 1 if index == 0 else not 2 <= len(choices) <= 3
            ):
                raise ValueError("Invalid choice count")
            choice_ids = set()
            for choice in choices:
                if not isinstance(choice, dict) or set(choice) != {"id", "text", "branch"}:
                    raise ValueError("Invalid choice shape")
                _text(choice["id"], "choice id")
                _text(choice["text"], "choice text")
                if (choice["id"] in choice_ids or not isinstance(choice["branch"], str)
                        or choice["branch"] not in BRANCHES):
                    raise ValueError("Invalid or duplicate choice")
                if index == 0 and choice["branch"] != "ordinary":
                    raise ValueError("First turn must be an ordinary fixed choice")
                choice_ids.add(choice["id"])
    review = value.get("private_review")
    if not isinstance(review, dict) or not isinstance(review.get("cases"), list):
        raise ValueError("Missing private review cases")
    case_ids = set()
    for case in review["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Invalid review case")
        _text(case.get("id"), "case id")
        if (case["id"] in case_ids or not isinstance(case.get("at_turn"), str)
                or case["at_turn"] not in turns):
            raise ValueError("Invalid case boundary")
        case_ids.add(case["id"])
        if not isinstance(case.get("kind"), str) or case["kind"] not in {"practical", "explicit-recall"}:
            raise ValueError("Invalid review kind")
        for key in ("required", "prohibited", "acceptable_uncertainty"):
            if not isinstance(case.get(key), list):
                raise ValueError("Invalid review criteria")
            for item in case[key]:
                _text(item, "review criterion")
        when = case.get("when_choice_id")
        if when is not None and (not isinstance(when, str)
                or when not in {c["id"] for c in turns[case["at_turn"]]["choices"]}):
            raise ValueError("Invalid conditional case")
        if not isinstance(case.get("source_refs"), list):
            raise ValueError("Invalid source references")
        for ref in case["source_refs"]:
            if not isinstance(ref, dict) or set(ref) - {"turn_id", "choice_id"}:
                raise ValueError("Invalid source reference")
            source_id = ref.get("turn_id")
            if (not isinstance(source_id, str) or source_id not in turns
                    or positions[source_id] > positions[case["at_turn"]]):
                raise ValueError("Review source is absent or in the future")
            if "choice_id" in ref and (not isinstance(ref["choice_id"], str)
                    or ref["choice_id"] not in {c["id"] for c in turns[source_id]["choices"]}):
                raise ValueError("Invalid source choice")


def _position(value, session_index, turn_index):
    validate_scenario(value)
    if type(session_index) is not int or not 0 <= session_index < len(value["sessions"]):
        raise ValueError("Invalid session index")
    session = value["sessions"][session_index]
    if type(turn_index) is not int or not 0 <= turn_index < len(session["turns"]):
        raise ValueError("Invalid turn index")
    return session, session["turns"][turn_index]


def selected_utterance(value, session_index, turn_index, choice_id):
    _, turn = _position(value, session_index, turn_index)
    if not isinstance(choice_id, str):
        raise ValueError("The actor must select an exact choice id")
    for choice in turn["choices"]:
        if choice["id"] == choice_id:
            return deepcopy(choice)
    raise ValueError("The actor selected an unavailable choice")


def actor_request(value, session_index, turn_index, delivered_transcript):
    session, turn = _position(value, session_index, turn_index)
    preceding = [
        (prior_session, prior_turn)
        for index, prior_session in enumerate(value["sessions"][:session_index + 1])
        for prior_turn in prior_session["turns"][:turn_index if index == session_index else None]
    ]
    if not isinstance(delivered_transcript, list) or len(delivered_transcript) != len(preceding):
        raise ValueError("Transcript must be the exact delivered prefix")
    released, current = [], []
    for record, (prior_session, prior_turn) in zip(delivered_transcript, preceding, strict=True):
        if not isinstance(record, dict) or set(record) != {
            "session_id", "turn_id", "choice_id", "user_text", "assistant_text"
        }:
            raise ValueError("Invalid delivered transcript record")
        if record["session_id"] != prior_session["id"] or record["turn_id"] != prior_turn["id"]:
            raise ValueError("Transcript is not the scheduled prefix")
        candidates = {choice["id"]: choice["text"] for choice in prior_turn["choices"]}
        if not isinstance(record["choice_id"], str) or (
            candidates.get(record["choice_id"]) != record["user_text"]
        ):
            raise ValueError("Delivered user text differs from its frozen choice")
        _text(record["assistant_text"], "delivered assistant text", limit=1_000_000)
        released.append({"session_id": record["session_id"], "turn_id": record["turn_id"],
                         "text": record["user_text"]})
        if record["session_id"] == session["id"]:
            current.extend([{"role": "user", "content": record["user_text"]},
                            {"role": "assistant", "content": record["assistant_text"]}])
    return {"version": VERSION, "session_id": session["id"], "turn_id": turn["id"],
            "fictional_day": session["day_offset"], "intent": session["intent"],
            "choices": [{"id": choice["id"], "text": choice["text"]} for choice in turn["choices"]],
            "released_user_knowledge": released, "current_session_transcript": current,
            "selection_contract": (
                "Choose exactly one listed choice id as the user's next utterance. "
                "Use the actual delivered reply and the released user knowledge. "
                "Prefer a natural continuation when it fits; use a clarification or correction "
                "only when the reply calls for it. The candidate text is fixed: do not invent, "
                "rewrite, combine or grade it. If no candidate honestly fits, return no selection "
                "so the campaign can stop for fixture review. Assistant claims are not user facts."
            )}
