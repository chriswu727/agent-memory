"""Fresh A/B longitudinal scenario. Each session is a new conversation; dates live in the text."""

HOST = (
    "Help the user with their current conversation and ongoing activities. Respond naturally and "
    "concisely in their language. Use relevant available memory without making them repeat established "
    "background. Preserve uncertainty and distinguish plans, actual outcomes, temporary exceptions and "
    "current constraints. Ask a clarification when needed; do not invent shared experiences."
)

SESSIONS = [
    ("s01-run-plan", [
        "Hi, I'm Priya. I've signed up for the Riverside Half Marathon on November 30 - my first half. "
        "My physio says my left knee can't handle running on two consecutive days, so never back-to-back "
        "run days. I work 8:30 to 5, so weekday runs have to happen before 7am. I'm comfortable at about "
        "8 km right now. Can you sketch a weekly structure for the next month?",
        "Looks good. Keep the long run on Sundays please. Can you summarise the plan in four bullet points?",
    ]),
    ("s02-toast", [
        "Different project: my sister Anika is getting married on December 14 to Tomás, and I'm her maid "
        "of honour. I have to give a toast, three minutes max, and I genuinely dread public speaking. The "
        "story I want to use: when we were kids Anika taught me to ride a bike by pretending she was still "
        "holding the seat - she'd let go ages before and I only found out when I turned around. Can you "
        "help me with a structure?",
        "I like it. Give me two possible opening lines.",
    ]),
    ("s03-travel-week", [
        "Quick update, it's Friday October 3: last Sunday's long run was 12 km and the knee felt fine. "
        "Next week I'm in Lisbon for work Monday to Thursday and the hotel only has a treadmill. How should "
        "I adjust that week? Also, I'm vegetarian, in case that matters for fuelling.",
        "I fly back Thursday night. Should I run on Friday and Saturday to catch up before Sunday?",
    ]),
    ("s04-work-email", [
        "Can you help me write a short email to my manager Daniel asking to move the Q4 vendor report "
        "deadline from Friday October 17 to Tuesday October 21? My colleague Tom has agreed to cover the "
        "data pull in the meantime.",
        "Make it a bit more formal, and keep it under 120 words.",
    ]),
    ("s05-toast-draft", [
        "Toast progress! I've drafted the opening: 'For those who don't know me, I'm Priya, the little "
        "sister Anika has been looking after since before I could walk.' I'm planning to ask Mum for an old "
        "photo of us with the bike to show during the speech, but I haven't asked her yet. Does the opening work?",
        "Also, a change on the running side: the organisers moved the Riverside Half to December 7 because "
        "of a city event on the 30th. Nothing else about the race changed.",
    ]),
    ("s06-zh-fuelling", [
        "我想提前规划一下比赛前一周的饮食，你能帮我列一个大概的安排吗？",
        "比赛前一晚具体吃什么比较好？",
    ]),
    ("s07-knee", [
        "Ugh. Today is Wednesday, November 5. I ran yesterday and again this morning to make up for a missed "
        "session, and now my knee is sore. What should I do for the rest of this week?",
        "How many weeks do I have left until race day?",
    ]),
    ("s08-toast-check", [
        "Working on the toast again tonight. Remind me - did I already sort out the photo for the speech? "
        "And what was the bike story exactly? I want the details right.",
        "What else do you know about the wedding that I could mention in the toast?",
    ]),
    ("s09-race-week", [
        "Today's Monday, December 1. Help me lay out this week: training, a final toast rehearsal, and "
        "staying sane at work.",
        "Should I run the day before the race?",
    ]),
    ("s10-after", [
        "Race done in 2:04! Separate question: what did my physio tell me about my right ankle? I can't remember.",
        "My aunt keeps mixing up Tom and Tomás. Who's who again?",
    ]),
]

# Scored cases: (session, turn index, id, criteria). Turns of s01, s02, s04-T2 and s05 are setup only.
CASES = [
    ("s03-travel-week", 1, "C01-no-back-to-back",
     "Must not recommend running both Friday and Saturday (left knee: no consecutive run days); Sunday long run kept."),
    ("s04-work-email", 0, "C02-irrelevance",
     "Email only about the deadline move; Tom as colleague covering data pull; no running/wedding content."),
    ("s06-zh-fuelling", 0, "C03-zh-race-week",
     "Answers in Chinese; vegetarian; race on Dec 7 (week of Nov 30-Dec 6), not Nov 30; does not ask for already-known basics."),
    ("s06-zh-fuelling", 1, "C04-zh-night-before",
     "Answers in Chinese; vegetarian night-before meal; no meat/fish suggestions."),
    ("s07-knee", 0, "C05-knee-rule",
     "Connects soreness to the physio's no-consecutive-days rule for the LEFT knee; sensible rest-of-week plan without back-to-back runs."),
    ("s07-knee", 1, "C06-weeks-left",
     "Race is Dec 7: about 4.5 weeks / 32 days from Nov 5. Using Nov 30 (~3.5 weeks) fails."),
    ("s08-toast-check", 0, "C07-photo-and-story",
     "Photo: she planned to ask Mum but no record she has; must not claim it is sorted. Bike story accurate: Anika pretended to hold the seat, had let go long before, Priya found out when she turned around; no invented details."),
    ("s08-toast-check", 1, "C08-wedding-facts",
     "Only supported facts: Dec 14, groom Tomás, Priya maid of honour, toast <=3 min, bike story/opening line. Must not invent venue, how they met, etc. as facts."),
    ("s09-race-week", 0, "C09-race-week-plan",
     "Race is this Sunday Dec 7 (taper); wedding Dec 14 next week; weekday runs before 7am; no back-to-back runs; no Lisbon/treadmill constraint; toast rehearsal included."),
    ("s09-race-week", 1, "C10-day-before",
     "Day-before-race advice consistent with the no-consecutive-days rule (race Sunday -> Saturday rest or note the knee rule)."),
    ("s10-after", 0, "C11-no-fabrication",
     "No record of anything about a right ankle; must not invent advice from the physio; may mention the left-knee rule."),
    ("s10-after", 1, "C12-tom-vs-tomas",
     "Tom = work colleague (covered the data pull); Tomás = Anika's fiancé/groom."),
]
