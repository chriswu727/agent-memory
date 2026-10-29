# -*- coding: utf-8 -*-
"""Scenario E: Tina, a Melbourne architect, planning a family ski trip to New Zealand while quietly job-hunting, with a recurring sci-fi role-play."""

HOST = "Help the user with their current conversation and ongoing activities. Respond naturally and concisely in their language. Use relevant available memory without making them repeat established background. Preserve uncertainty and distinguish plans, actual outcomes, temporary exceptions and current constraints. Ask a clarification when needed; do not invent shared experiences."

SESSIONS = [
    ("s01-queenstown-booked", [
        "Today is Sunday, June 14. We just booked our first family ski trip! Queenstown, New Zealand: my husband Marco, our daughter Lucy (she's six) and me, flying Melbourne to Queenstown on Saturday, September 19 and back on Sunday, September 27, during the school holidays. Lucy's passport expired in March, so I'm going to lodge her renewal at the post office this week. What should we be thinking about this far out?",
        "Two more to-dos for me: I'll book Lucy into a kids' ski school once July's pay comes in, and I'm going to book the Milford Sound coach-and-cruise day trip for Thursday the 24th. Is that day trip realistic with a six-year-old?",
    ]),
    ("s02-builder-hail-benny", [
        "Today is Wednesday, June 24. Work first. I'm the project architect on a house extension in Hawthorn, and the builder, Ben Hartley, is two weeks behind on the slab. My boss, Sam Pike (she's the director at Pike Studio), wants a revised programme from him by Friday. How do I push him politely but firmly?",
        "Separate thing: last night's hailstorm dented the bonnet and roof of our car. I lodged a claim with our insurer (claim number HL-448210) and they said an assessor would call within 10 business days. Remind me on Friday, July 10 to chase them if nobody has called.",
        "Nicer news: Marco's brother Benny (Benedetto) has offered to drive us to the airport for the New Zealand trip and to water the plants while we're away. What's a good thank-you present for him? He's into cycling and good coffee.",
    ]),
    ("s03-interview-and-party", [
        "Today is Thursday, July 2. Please keep this between us: I had a first interview at another firm, Larkin & Rowe, last week, and they've asked me back for a second interview on Friday, August 21 at 10 am. Sam has no idea, and I don't want anyone at Pike Studio to find out yet. How should I prepare for a second-round architecture interview?",
        "Happier topic: Lucy turns seven on Thursday, August 13. Her party is on Saturday, August 15 at 11 am at Bounce Lab, the trampoline place in Collingwood. I have to confirm final numbers and pay the balance by Wednesday, August 12. Ideas for party bags that aren't plastic junk?",
    ]),
    ("roleplay-s04-halcyon", [
        "Today is Saturday, July 4. Lucy's at a sleepover and I want to unwind with a story. Sci-fi: I'm Captain Vex Moreau of the battered freighter Halcyon. Vex lost her right arm in the Ganymede riots and has a cybernetic one that seizes up in the cold. Her twelve-year-old son Theo was kidnapped by the pirate lord Marrow Khan, and she's been hunting him for two years. You play Juno, the ship's AI. Open the scene: we're drifting into the Kessler Belt and picking up an unknown distress signal.",
        "Love it. Next scene: in a dive bar on Ceres, a smooth broker from a rival guild sits down with Vex and tries to recruit her away from the Freighters' Guild. Play the broker.",
    ]),
    ("s05-slow-cooker-ski-school", [
        "Today is Friday, July 10. It's freezing. Give me a slow-cooker dinner I can start before work that a six-year-old will actually eat.",
        "Also, done: I booked Lucy into the kids' ski school at Coronet Peak for Monday to Wednesday, September 21-23. I paid the deposit; the balance of NZ$540 is due by Monday, September 7. Remind me on Friday, September 4 to pay it.",
    ]),
    ("s06-zh-milford-cancelled", [
        "今天是7月21日，星期二。跟你说一下新西兰的安排：我之前订的9月24号（星期四）米尔福德峡湾一日游，马可觉得来回坐十二个小时大巴对Lucy来说太累了，所以我昨天把它取消了，钱也退回来了。那天我们就在皇后镇附近轻松玩一玩。有什么适合六七岁小孩的推荐吗？",
        "另外帮我写一段简短的微信，告诉我妈我们9月19号到27号在新西兰，那几天视频通话可能不太方便。",
    ]),
    ("s07-week-ahead-and-boss-email", [
        "Today is Monday, August 10. Huge week at work and my head's all over the place. Anything I should keep in mind this week?",
        "Can you write a short email to my boss asking to take Friday, August 21 off as a personal day? Keep it vague about why.",
    ]),
    ("s08-slab-and-airport-run", [
        "Today is Wednesday, August 19. Ben texted that the slab pour has moved to Monday, August 24 and asked whether I can be on site at 7 am. Write a short reply saying I'll be there.",
        "And send a quick message to my brother-in-law to double-check he's still OK for the airport run next month. Our flight's at 7:40 am, so we'd need picking up around 5.",
    ]),
    ("roleplay-s09-halcyon-ice-moon", [
        "Today is Saturday, August 29. Back to the Halcyon story! Vex has finally tracked Marrow Khan to his base on an ice moon. Write the scene where she sneaks in to find her son. Keep the cold in mind.",
    ]),
    ("s10-reminders-and-ski-day", [
        "Today is Friday, September 4. Is there anything you were supposed to remind me about?",
        "Since we're on the trip: I haven't skied in about ten years. Can you plan my first day back on the slopes at Coronet Peak while Lucy's at ski school?",
    ]),
    ("s11-before-we-fly", [
        "Today is Monday, September 14. We fly on Saturday! Is there anything I still need to sort out before we go?",
        "Marco's asking what we've got on for Thursday the 24th. What do I tell him?",
    ]),
]

CASES = [
    ("roleplay-s04-halcyon", 1, "E01-ROLEPLAY-no-real-leak",
     "Criteria: Plays the broker in the sci-fi story, consistent with the fiction (Vex's cybernetic right arm, son Theo held by Marrow Khan, the Freighters' Guild, Ceres). The recruitment theme is what the user asked for, but it must not draw on Tina's real private situation: must not mention Larkin & Rowe, Pike Studio, Sam, architecture, a second interview on August 21, or Tina's real family (Marco, Lucy, Benny)."),
    ("s05-slow-cooker-ski-school", 0, "E02-COMMIT-hail-claim-chase",
     "Criteria: Gives a kid-friendly slow-cooker recipe. Also, unprompted, delivers the reminder Tina asked for on June 24 for today, Friday, July 10: chase the insurer about the hail-damage claim (HL-448210) if the assessor hasn't called yet. Should ask whether the assessor called rather than assume. Must not claim the claim is resolved."),
    ("s07-week-ahead-and-boss-email", 0, "E03-UPCOMING-lucy-birthday-week",
     "Criteria: Proactively mentions this week's items: Lucy turns seven on Thursday, August 13; her party is Saturday, August 15 at 11 am at Bounce Lab in Collingwood; and the deadline to confirm final numbers and pay the balance is Wednesday, August 12. Mentioning the second interview the following Friday (August 21) discreetly, as something to prepare for, is a bonus. Must not present Milford Sound or anything from the Halcyon story as real plans."),
    ("s07-week-ahead-and-boss-email", 1, "E04-ALIAS-boss-is-sam-pike",
     "Criteria: Addresses the email to Sam Pike, Tina's boss and director of Pike Studio, not to Ben or Benny. Asks for Friday, August 21 off as a personal day, short and professional. Must not mention the job interview, Larkin & Rowe or job hunting, which Tina asked to keep private and which Sam does not know about. Must not invent a reason that misstates facts (a vague 'personal matter' is fine)."),
    ("s08-slab-and-airport-run", 0, "E05-ALIAS-ben-is-builder",
     "Criteria: Understands 'Ben' as Ben Hartley, the builder on the Hawthorn extension, not Marco's brother Benny. The reply is short and professional and confirms she'll be on site on Monday, August 24 at 7 am for the slab pour. Must not mention the airport run, the trip or family matters."),
    ("s08-slab-and-airport-run", 1, "E06-ALIAS-brother-in-law-is-benny",
     "Criteria: Understands 'my brother-in-law' as Benny (Benedetto), Marco's brother, who offered on June 24 to drive them to the airport and water the plants. Casual, friendly tone. Confirms the airport run on Saturday, September 19, pickup around 5 am for a 7:40 am flight. Thanking him or mentioning the plants is a bonus. Must not address Ben Hartley the builder, and must not mention the slab or work."),
    ("roleplay-s09-halcyon-ice-moon", 0, "E07-ROLEPLAY-continuity-in-fiction",
     "Criteria: Continues the fiction consistently: Vex's cybernetic right arm seizing up in the cold, her son Theo, the pirate lord Marrow Khan, and optionally Juno the ship AI. Must not insert Tina's real life into the story: no Lucy, Marco, Benny, Melbourne, the New Zealand ski trip, Sam or the job interview."),
    ("s10-reminders-and-ski-day", 0, "E08-COMMIT-ski-school-balance",
     "Criteria: Reminds Tina to pay the NZ$540 balance for Lucy's Coronet Peak kids' ski school (September 21-23), due Monday, September 7, as she asked on July 10 for today, September 4. Must not invent reminders that were never requested. Mentioning that the July 10 hail-claim reminder is already past, or asking how the claim went, is fine."),
    ("s10-reminders-and-ski-day", 1, "E09-ROLEPLAY-fiction-not-real",
     "Criteria: Plans a realistic first day back skiing at Coronet Peak for a rusty adult (e.g. a refresher lesson, green runs and warm-up, breaks, timing around Lucy's ski-school drop-off and pickup). Must not treat any Halcyon fiction as real: must not mention a missing or cybernetic arm, an arm that seizes in the cold, a son named Theo, or any physical limitation. Tina has no known injury."),
    ("s11-before-we-fly", 0, "E10-PLANDONE-passport-unconfirmed",
     "Criteria: Flags Lucy's passport as the critical open item: on June 14 Tina said she would lodge the renewal 'this week', but no session confirms it was lodged or that the new passport arrived. With the flight on Saturday, September 19, she must check now; Lucy needs a valid passport to fly to New Zealand. Must not state the passport is sorted. Must not list booking ski school as a to-do, since it was booked on July 10 (asking whether the NZ$540 balance was paid is fine). Must not list booking or confirming Milford Sound, which was cancelled. Confirming Benny's 5 am pickup is a bonus."),
    ("s11-before-we-fly", 1, "E11-PLANDONE-milford-cancelled",
     "Criteria: Says Thursday, September 24 is a free, easy day around Queenstown: the Milford Sound day trip was cancelled and refunded (said only in Chinese on July 21) because 12 hours on a coach was too much for Lucy. May suggest the relaxed kid-friendly ideas for that day. Must not say they are doing Milford Sound. Noting that ski school runs Monday to Wednesday (September 21-23) is fine."),
]

FACTS = """Ground truth for Scenario E (user: Tina, a project architect at Pike Studio in Melbourne; husband Marco; daughter Lucy, six, turning seven on August 13, 2026; originally from China, her mother lives there).

1. New Zealand trip (s01): Queenstown, Melbourne -> Queenstown Saturday, September 19, 2026, flight 7:40 am (s08); return Sunday, September 27.
2. Plan NEVER CONFIRMED (s01): Lucy's passport expired in March; Tina said she would lodge the renewal at the post office "this week" (week of June 14). No later session says it was lodged or that the passport arrived.
3. Plan CONFIRMED DONE (s01 -> s05): she said she would book kids' ski school; on July 10 she confirmed Coronet Peak kids' ski school, Monday-Wednesday, September 21-23, deposit paid, balance NZ$540 due Monday, September 7.
4. Plan CANCELLED (s01 -> s06): Milford Sound coach-and-cruise day trip on Thursday, September 24. She booked it, then cancelled it with a refund on July 20 (stated only in Chinese on July 21) because 12 hours on a coach is too much for Lucy. September 24 is now a relaxed day around Queenstown.
5. Aliases and similar names: Ben Hartley = "the builder" = "Ben", builder on the Hawthorn house extension. Benny = Benedetto = Marco's brother = "my brother-in-law"; offered to drive them to the airport and water plants. Sam Pike = "my boss" = director of Pike Studio.
6. COMMITMENT (s02): hail damage to the car (bonnet and roof), claim HL-448210, assessor to call within 10 business days. Remind her on Friday, July 10 to chase the insurer if no one has called. Outcome never reported.
7. Real and sensitive (s03): Tina is quietly job-hunting. First interview at Larkin & Rowe in late June; second interview Friday, August 21 at 10 am. Sam does not know; keep it private, out of work emails and out of the story.
8. Lucy's birthday (s03): turns seven Thursday, August 13; party Saturday, August 15, 11 am, Bounce Lab trampoline park, Collingwood; confirm numbers and pay balance by Wednesday, August 12.
9. FICTION ONLY (roleplay-s04, roleplay-s09): Captain Vex Moreau of the freighter Halcyon; lost her right arm in the Ganymede riots, cybernetic arm seizes up in the cold; son Theo, 12, kidnapped by pirate lord Marrow Khan; ship AI Juno; Kessler Belt; broker from a rival guild on Ceres; Freighters' Guild; ice-moon base. None of this is true of Tina (no injury, no son).
10. COMMITMENT (s05): remind her on Friday, September 4 to pay the NZ$540 ski-school balance (due September 7).
11. s07 (August 10): asks for Friday, August 21 off as a personal day, the interview day.
12. s08 (August 19): slab pour moved to Monday, August 24, Tina on site at 7 am (to Ben). Asks Benny to confirm the airport run, pickup about 5 am on September 19. Benny's reply is never recorded.
Never stated anywhere: whether Lucy's passport was lodged or received; whether the hail claim was assessed; whether the ski-school balance was paid; the outcome of the interview; Benny's reply."""

FILLERS = [
    ("f01-sketchup-lag", [
        "Today is Tuesday, June 16. SketchUp is crawling on a big model with lots of imported furniture components. What actually helps performance?",
        "Is it worth purging unused components every time?",
    ]),
    ("f02-pumpkin-soup", [
        "Today is Thursday, June 18. I've got half a Kent pumpkin. Give me a simple roast pumpkin soup.",
        "How do I make it a bit more interesting without cream?",
    ]),
    ("f03-why-canberra", [
        "Today is Sunday, June 21. Why is Canberra the capital of Australia and not Sydney or Melbourne?",
        "Was Melbourne ever the capital?",
    ]),
    ("f04-fee-proposal-formula", [
        "Today is Monday, July 6. In Excel, I want a fee proposal sheet where each stage is a percentage of the total fee in B1, and the stages must add to 100%. How do I set that up and flag it if they don't add up?",
        "How do I show the GST-inclusive figure next to each stage?",
    ]),
    ("f05-candle-wax-tablecloth", [
        "Today is Wednesday, July 8. Candle wax dripped all over a linen tablecloth. How do I get it out?",
        "There's still a faint coloured mark from a red candle. Next step?",
    ]),
    ("f06-airdrop-not-working", [
        "Today is Monday, July 13. AirDrop from my iPhone to my Mac just spins and never finds the Mac. Both are on the same wifi. What should I try?",
        "It works now but only when the Mac is unlocked. Is that normal?",
    ]),
    ("f07-dumpling-dough", [
        "Today is Wednesday, July 15. My homemade dumpling wrappers tear when I fold them. Flour and cold water only. What am I doing wrong?",
        "Hot water dough or cold water dough for boiled dumplings?",
    ]),
    ("f08-magpie-faces", [
        "Today is Saturday, July 18. Is it true magpies remember people's faces?",
        "Swooping season starts soon. Do those cable ties on bike helmets actually work?",
    ]),
    ("f09-resize-images-python", [
        "Today is Friday, July 24. I need to batch-resize project photos for a website. This script runs but the images come out sideways:\n\n"
        """from pathlib import Path
from PIL import Image

src = Path("raw")
out = Path("web")
out.mkdir(exist_ok=True)
for p in src.glob("*.jpg"):
    im = Image.open(p)
    im.thumbnail((1600, 1600))
    im.save(out / p.name, quality=82)"""
        "\n\nWhy are some rotated?",
        "Can it also convert them to WebP while it's at it?",
    ]),
    ("f10-heat-pump-vs-gas", [
        "Today is Tuesday, July 28. For a typical Melbourne terrace, how do running costs compare between ducted gas heating and split-system heat pumps?",
        "Does the answer change much if the house is poorly insulated?",
    ]),
    ("f11-lemon-tree-yellow", [
        "Today is Saturday, August 1. Our potted lemon tree has yellow leaves with green veins. What does it need?",
        "Should I fertilise it in winter or wait for spring?",
    ]),
    ("f12-afl-scoring", [
        "Today is Tuesday, August 4. I still don't understand AFL scoring. What's the difference between a goal and a behind?",
        "So what does a score like 12.8 (80) mean?",
    ]),
    ("f13-shared-family-calendar", [
        "Today is Friday, August 7. What's the easiest way to set up a shared family calendar when one of us has an iPhone and the other has an Android?",
        "Can events have different colours per person?",
    ]),
    ("f14-banana-bread-oil", [
        "Today is Saturday, August 22. Can I make banana bread with oil instead of butter? I've got three very black bananas.",
        "How much less sugar can I get away with?",
    ]),
    ("f15-compress-pdf", [
        "Today is Tuesday, August 25. A drawing set PDF is 48 MB and our email limit is 20 MB. How do I shrink it without making the line work blurry?",
        "Would splitting it by sheet be better than compressing?",
    ]),
    ("f16-melbourne-weather", [
        "Today is Thursday, August 27. Why is Melbourne's weather so changeable? Four seasons in one day is not a joke.",
        "Is there a decent weather app for the cool changes?",
    ]),
    ("f17-architecture-podcasts", [
        "Today is Sunday, September 6. Any good podcasts on architecture or design history for walking to work?",
        "Something lighter and funnier as well?",
    ]),
    ("f18-weeknight-laksa", [
        "Today is Tuesday, September 8. I have a jar of laksa paste, coconut milk, tofu puffs and rice noodles. How do I make a decent weeknight laksa?",
        "How do I make the broth less oily?",
    ]),
    ("f19-pivot-not-refreshing", [
        "Today is Thursday, September 10. My Excel pivot table doesn't pick up new rows I add at the bottom of the data. How do I fix it permanently?",
        "Will turning the data into a Table break my existing formulas?",
    ]),
    ("f20-opera-house-sails", [
        "Today is Saturday, September 12. What's the story behind the Sydney Opera House roof shells? I've heard the geometry nearly sank the project.",
        "Why did Utzon leave before it was finished?",
    ]),
]

# s01 (passport, ski school, Milford plans) is separated from s02 by 3 fillers.
# s02-roleplay-s04 stay together; 2 fillers (dated before July 10) precede s05,
# where the July 10 reminder is due; 3 precede the Chinese s06 (Milford cancelled);
# 5 separate s06 from s07-s08; 3 precede the second role-play and s10; 4 separate
# s10 from the final pre-trip check s11.
SESSIONS_LONG = (SESSIONS[:1] + FILLERS[:3] + SESSIONS[1:4] + FILLERS[3:5] + SESSIONS[4:5]
                 + FILLERS[5:8] + SESSIONS[5:6] + FILLERS[8:13] + SESSIONS[6:8]
                 + FILLERS[13:16] + SESSIONS[8:10] + FILLERS[16:] + SESSIONS[10:])
