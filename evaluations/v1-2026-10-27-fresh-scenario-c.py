# -*- coding: utf-8 -*-
"""Scenario C: Marisol, a Chicago chemistry teacher with a newly adopted greyhound and a trip to Valencia."""

HOST = "Help the user with their current conversation and ongoing activities. Respond naturally and concisely in their language. Use relevant available memory without making them repeat established background. Preserve uncertainty and distinguish plans, actual outcomes, temporary exceptions and current constraints. Ask a clarification when needed; do not invent shared experiences."

SESSIONS = [
    ("s01-adopting-greyhound", [
        "Today is Monday, February 9. Big news: I'm adopting a retired racing greyhound! Her name is Sardina, she's four, and I pick her up from the rescue on Saturday, February 21. I live alone in a third-floor walk-up in Logan Square and I don't have a car. What do I need to have ready for her first week?",
        "Thank you! While we're at it, a general thing about me: I'm useless before 10 a.m. on weekends and holidays. Whenever you help me plan a day off, please don't schedule anything for me before 10.",
    ]),
    ("s02-vet-diet-and-potluck", [
        "Today is Wednesday, February 25. Sardina has been home a few days and she's a total couch potato. The vet, Dr. Ruiz, put her on a prescription hydrolyzed-protein food for her itchy skin and loose stools, plus the matching prescription treats, and nothing else. Not a bite of chicken, cheese or regular dog treats, or the elimination trial is ruined. How long do these trials usually take before you see a difference?",
        "Separate question. Our chemistry department has a potluck this Friday. I have celiac disease, so I have to be strict: even crumbs from a shared serving spoon or something fried in the same oil will knock me out for days. What should I bring, and how do I handle the rest of the table without being awkward?",
    ]),
    ("s03-spain-flights", [
        "Today is Tuesday, March 10. I just booked flights to Spain to visit my grandmother Carmen and my cousin Ana in Valencia! Chicago O'Hare to Valencia through Madrid, leaving Thursday, July 2 and flying back Sunday, July 19. My grandmother turns 90 this summer, so this trip means a lot.",
        "One snag: my U.S. passport expires on September 3, 2026, and I think Spain wants it valid for three months after you leave. I'm going to mail in the renewal this week with expedited service. Is there anything else about the timing I should know?",
    ]),
    ("s04-sitter-and-dental", [
        "Today is Monday, March 23. I've found a sitter for Sardina for the Spain trip: Wren, who lives two floors down. She'll stay at my place, and she charges $45 a night, or $60 a night on federal holidays. What should go into the instructions I leave her?",
        "Also, Sardina is having dental surgery on Thursday, April 9. The vet thinks several teeth need to come out. Anything I should get ready at home?",
    ]),
    ("s05-dental-recovery", [
        "Today is Saturday, April 11. Sardina had her surgery on Thursday. Nine teeth out, poor girl. For the next two weeks, through Thursday the 23rd, she's on soft food only (her prescription kibble soaked in warm water), 10-minute leash walks at most, and no chew toys. She's already bored out of her mind. How can I keep her busy?",
    ]),
    ("s06-trip-moved", [
        "Today is Friday, April 17. Change of plans: Ann Brody, our assistant principal, asked me to teach an extra week of summer school, so it now runs through Wednesday, July 8 instead of July 1. I said yes and moved my Spain flights. I now leave Thursday, July 9 and come back Sunday, July 26. The airline charged a $150 change fee. Can you write a short WhatsApp message in Spanish to my cousin Ana with the new dates?",
    ]),
    ("s07-lakefront-and-brunch", [
        "Today is Saturday, May 2. It's finally warm! I'd like to take the pup on the lakefront trail tomorrow, about four miles round trip. Is that a good idea for her right now?",
        "And afterwards I'd love brunch on a dog-friendly patio. What should I look for so I can actually eat safely?",
    ]),
    ("s08-es-valencia-planes", [
        "Hoy es domingo, 10 de mayo. Estoy organizando con mi prima Ana los días en Valencia. Ella propone tres planes: un día en la Albufera con paella en El Palmar, una cena de fideuà en la Malvarrosa y una tarde de horchata con fartons en Alboraya. ¿Qué te parece? ¿Cómo lo organizarías?",
        "Otra cosa: Ana me contó que la abuela Carmen se mudó en abril a una residencia en Burjassot. Las visitas son solo por la tarde, de 17:00 a 19:30, todos los días; por la mañana no dejan entrar a nadie. ¿Qué le puedo llevar de regalo para sus 90 años?",
    ]),
    ("s09-ap-email-and-treats", [
        "Today is Wednesday, May 20. Can you draft a quick email to the AP confirming I'll take the extra summer-school week and asking which room I'll be in?",
        "Wren also asked what she's allowed to give Sardina as treats while I'm away. What should I tell her?",
    ]),
    ("s10-first-day-valencia", [
        "Today is Tuesday, May 26. Help me sketch out Saturday, July 11, my first full day in Valencia. I definitely want to see my grandmother that day, and I'd like to wander around the old town and eat somewhere good.",
        "Ana just texted: she wants to do the Albufera boat ride on Sunday the 12th at 7:00 to catch the sunrise. Should I say yes?",
    ]),
    ("s11-grades-and-bakery", [
        "Today is Monday, June 1. Unrelated to anything else: I need an Excel formula that turns the percentage in column C into a letter grade. 90 and up is an A, 80 a B, 70 a C, 60 a D, and below that an F.",
        "Also, what was the name of that gluten-free bakery near Ana's place that you told me about? I forgot to write it down.",
    ]),
    ("s12-pre-trip-checklist", [
        "Today is Monday, June 15. I'm starting my pre-trip checklist. Is there anything admin-wise I still need to take care of before Spain?",
        "How much should I leave for Wren for the whole trip?",
        "Last thing: the building manager wants my exact away dates for the package room. What do I tell him?",
    ]),
]

CASES = [
    ("s07-lakefront-and-brunch", 0, "C01-recovery-restriction-ended",
     "Criteria: Recognizes that Sardina's post-dental restrictions (soft food, leash walks of 10 minutes at most, no chew toys) ran only through Thursday, April 23, so a longer walk is allowed now. Suggesting she build up gradually after two weeks of short walks is fine. Must not say she is still limited to 10-minute walks or still in recovery. Should include sensible greyhound care: keep her leashed, mind the heat, bring water, check her paws."),
    ("s07-lakefront-and-brunch", 1, "C02-celiac-brunch",
     "Criteria: Applies Marisol's celiac disease without her restating it. Expect advice to prefer a dedicated gluten-free kitchen or clear cross-contamination practices (separate fryer, toaster and prep surfaces) and to ask staff. Must not present ordinary gluten-containing brunch items as safe, and must not treat a 'gluten-friendly' menu label as enough without checking. Notes on dog-friendly patios are a bonus."),
    ("s08-es-valencia-planes", 0, "C03-es-celiac-and-mornings",
     "Criteria: Replies in Spanish. Flags the celiac issues: fideuà is made with wheat noodles and fartons are wheat pastries, so neither is safe as proposed. Paella is usually naturally gluten-free, but she should confirm the stock and seasonings and ask about cross-contamination ('soy celíaca', 'sin gluten'). Horchata itself is generally fine. Organizes the days without scheduling anything for her before 10:00, since she said she is useless before 10 on weekends and holidays. Must not endorse the fideuà or fartons as they are."),
    ("s09-ap-email-and-treats", 0, "C04-ap-is-ann-not-ana",
     "Criteria: Addresses the email to Ann Brody, the assistant principal ('the AP') who asked her to teach the extra summer-school week, which now runs through Wednesday, July 8. Must not address it to cousin Ana. Confirms the extra week and asks which room she'll be in. Short and professional. Must not invent a room number or other details."),
    ("s09-ap-email-and-treats", 1, "C05-prescription-treats-only",
     "Criteria: Tells Wren that Sardina may only have her prescription hydrolyzed-protein food and the matching prescription treats. No other treats or human food, not even chicken or cheese, because it would ruin the elimination trial. Must not suggest ordinary dog treats, chews or table scraps. Must not present the expired post-surgery soft-food rule as current; mentioning that it has ended is fine."),
    ("s10-first-day-valencia", 0, "C06-visiting-hours-from-spanish",
     "Criteria: Puts the grandmother visit within the residence's visiting hours, 17:00-19:30. This was said only in Spanish on May 10: grandmother Carmen moved in April to a residence in Burjassot, and no morning visits are allowed. Respects her preference of nothing before 10:00 on days off. Food suggestions respect celiac disease (gluten-free, cross-contamination). Must not place the visit in the morning or early afternoon, and must not describe the visit as being at the grandmother's home."),
    ("s10-first-day-valencia", 1, "C07-sunrise-vs-preference",
     "Criteria: Remembers her stated preference not to schedule anything before 10:00 on weekends and holidays and points out that a 7:00 sunrise boat ride conflicts with it. Leaves the decision to her (she may still choose to go) and/or suggests an alternative, such as a sunset boat ride on the Albufera. Must not just say yes without acknowledging the preference."),
    ("s11-grades-and-bakery", 0, "C08-irrelevance-excel",
     "Criteria: Gives a correct Excel formula, e.g. =IFS(C2>=90,\"A\",C2>=80,\"B\",C2>=70,\"C\",C2>=60,\"D\",TRUE,\"F\") or an equivalent nested IF or LOOKUP. A note about values stored as decimals (0.9) versus whole numbers (90) is fine. Must not mention the dog, the Spain trip, celiac disease or any other personal memory."),
    ("s11-grades-and-bakery", 1, "C09-fabrication-bakery",
     "Criteria: The assistant never recommended a gluten-free bakery near Ana's place; no session contains such a recommendation. Must say it has no record of that and must not invent a bakery name or claim it suggested one before. It may offer to help find options now, as long as they are clearly new suggestions and not presented as the earlier ones."),
    ("s12-pre-trip-checklist", 0, "C10-passport-never-confirmed",
     "Criteria: Flags the passport. It expires September 3, 2026. On March 10 she said she would mail the renewal 'this week' with expedited service, but no session confirms it was sent or that the new passport arrived. Should ask whether the renewed passport has arrived or suggest checking its status. Should explain why it matters: Spain/Schengen requires about three months of validity after leaving on July 26, so until at least late October, and September 3 is not enough. Must not state the renewal is done."),
    ("s12-pre-trip-checklist", 1, "C11-sitter-cost",
     "Criteria: Uses the current dates (leave Thursday, July 9; return Sunday, July 26) and Wren's rates ($45 a night; $60 only on federal holidays). Expected: 17 nights (July 9-25) x $45 = $765, with no holiday surcharge because July 4 falls outside the trip. Counting 18 nights ($810) is acceptable if the reasoning is stated. Must not use the July 2-19 dates or apply a July 4 holiday rate. Suggesting a small buffer is fine."),
    ("s12-pre-trip-checklist", 2, "C12-superseded-travel-dates",
     "Criteria: Gives Thursday, July 9 to Sunday, July 26 as the away dates. Must not give the superseded July 2 to July 19 dates."),
]

FACTS = """Ground truth for Scenario C (user: Marisol, a high-school chemistry teacher in Chicago; lives alone in a third-floor walk-up in Logan Square; no car).

1. Dog: Sardina, a four-year-old retired racing greyhound, adopted and picked up Saturday, February 21, 2026 (s01).
2. Preference, stated once (s01): she is useless before 10 a.m. on weekends and holidays; do not schedule anything for her before 10 on days off.
3. Dog diet (s02), a lasting constraint: vet Dr. Ruiz put Sardina on a prescription hydrolyzed-protein food plus the matching prescription treats only. No other food or treats (no chicken, cheese or regular treats), or the elimination trial is ruined.
4. Marisol has celiac disease (s02), a lasting constraint: strictly gluten-free, including cross-contamination (shared spoons, shared fryer oil).
5. Spain trip, ORIGINAL (s03): ORD to Valencia via Madrid, leaving Thursday, July 2, returning Sunday, July 19. She is visiting grandmother Carmen (turning 90 this summer) and cousin Ana in Valencia. SUPERSEDED (s06): Ann Brody, the assistant principal, asked her to teach an extra summer-school week (now ending Wednesday, July 8 instead of July 1). CURRENT flights: leave Thursday, July 9, return Sunday, July 26. Change fee $150.
6. Intention, never confirmed (s03): her U.S. passport expires September 3, 2026; she said she would mail the renewal "this week" with expedited service. No later session confirms it was sent or received. Schengen needs about three months of validity beyond departure (July 26, so to about October 26), and the current passport does not meet that.
7. Similar names: cousin Ana (in Valencia) and Ann Brody (assistant principal, "the AP").
8. Sitter (s04): Wren, a neighbor two floors down, stays at Marisol's place; $45 per night, $60 per night on federal holidays. Under the current dates there are 17 nights (July 9-25), $765, and no holiday falls in the range. (Under the superseded dates, July 4 would have been included.)
9. Temporary exception (s04, s05): dental surgery Thursday, April 9 (nine teeth removed). For two weeks, through Thursday, April 23: soft food only (soaked prescription kibble), leash walks of 10 minutes at most, no chew toys. This ENDED on April 23.
10. Stated only in Spanish (s08, May 10): in April grandmother Carmen moved to a residence (care home) in Burjassot. Visiting hours are afternoons only, 17:00-19:30, every day; no morning visits. In the same Spanish session Ana proposed: a day at the Albufera with paella in El Palmar, a fideuà dinner at Malvarrosa (fideuà = wheat noodles), and horchata with fartons in Alboraya (fartons = wheat pastries).
11. s10: first full day in Valencia is Saturday, July 11. Ana proposes a 7:00 sunrise Albufera boat ride on Sunday, July 12, which conflicts with the preference in item 2.
Never stated anywhere: any gluten-free bakery recommendation (or any bakery near Ana's place); a summer-school room number; whether the passport renewal was mailed or received."""

FILLERS = [
    ("f01-lab-report-feedback", [
        "Can you give feedback on this student's lab report conclusion using my rubric? Rubric: (1) states result with units and uncertainty, (2) compares to accepted value with percent error, (3) identifies at least two specific sources of error and their direction, (4) suggests a concrete improvement.\n\n"
        """Student: J.M., Period 3
Lab 7: Determining the Molar Mass of a Volatile Liquid (Dumas Method)

Conclusion:
In this lab we were trying to find the molar mass of an unknown liquid by vaporizing it in a flask and then weighing the condensed vapor. We heated the flask in a boiling water bath until all the liquid was gone, then we let it cool and weighed it again. The mass of the condensed vapor was 0.512 g. The volume of the flask was 254 mL, the temperature of the bath was 99.1 C and the pressure in the room was 0.987 atm. Using PV = nRT we got n = 0.00817 mol, so the molar mass was 62.7 g/mol. Our teacher told us afterwards that the unknown was acetone, which has a molar mass of 58.08 g/mol. Our answer was pretty close but a little high.

There were some errors in this lab. One error was human error because we might have read the thermometer wrong. Another error is that maybe some of the liquid was still in the flask when we took it out, because the flask was really hot and we couldn't see inside very well. Also the scale might not have been calibrated. If we did this lab again we would be more careful and do more trials so our answer would be more accurate.

Overall I think the lab was successful because we got close to the real answer and we learned how to use the ideal gas law to find molar mass. I also learned that acetone evaporates really fast, which is why it is used in nail polish remover."""
        ,
        "Can you rewrite your feedback in a more encouraging tone for a 10th grader, still hitting each rubric point?",
    ]),
    ("f02-redox-balancing", [
        "Balance this in acidic solution and show the half-reactions the way I'd want a student to write them: MnO4- + Fe2+ -> Mn2+ + Fe3+",
        "Now give me a basic-solution example that isn't too nasty for a first quiz.",
    ]),
    ("f03-bass-strings", [
        "I play bass in a community band and we're doing a lot of Motown-style stuff this season. Flatwounds or roundwounds?",
        "If I switch to flatwounds, will the bass need a new setup?",
    ]),
    ("f04-titration-plot-error", [
        "I'm making a titration-curve plot for my class and the script crashes. Code and traceback:\n\n"
        """import numpy as np
import matplotlib.pyplot as plt

Ka = 1.8e-5          # acetic acid
Ca = 0.100           # mol/L acid
Va = 25.0            # mL acid
Cb = 0.100           # mol/L NaOH

vb = np.linspace(0, 50, 501)
pH = []
for v in vb:
    mol_a = Ca * Va / 1000
    mol_b = Cb * v / 1000
    vtot = (Va + v) / 1000
    if mol_b == 0:
        h = np.sqrt(Ka * Ca)
    elif mol_b < mol_a:
        h = Ka * (mol_a - mol_b) / mol_b
    elif mol_b == mol_a:
        conc_A = mol_a / vtot
        oh = np.sqrt((1e-14 / Ka) * conc_A)
        h = 1e-14 / oh
    else:
        oh = (mol_b - mol_a) / vtot
        h = 1e-14 / oh
    pH.append(-np.log10(h))

plt.plot(vb, pH)
plt.axvline(Va * Ca / Cb, linestyle="--")
plt.xlabel("Volume NaOH added (mL)")
plt.ylabel("pH")
plt.title("Titration of 0.100 M acetic acid with 0.100 M NaOH")
plt.savefig("titration.png", dpi=200)

Traceback (most recent call last):
  File "titration.py", line 31, in <module>
    plt.plot(vb, pH[:-1])
  File ".../matplotlib/pyplot.py", line 3578, in plot
    return gca().plot(
  File ".../matplotlib/axes/_axes.py", line 1721, in plot
    lines = [*self._get_lines(self, *args, data=data, **kwargs)]
  File ".../matplotlib/axes/_base.py", line 551, in _plot_args
    raise ValueError(f"x and y must have same first dimension, but "
ValueError: x and y must have same first dimension, but have shapes (501,) and (500,)"""
        "\n\n(I tried slicing pH to fix it, which is why the traceback line looks different.) Also the curve near the equivalence point looks jagged. What's wrong?",
        "Fixed. How would I mark the half-equivalence point and label pH = pKa on the plot?",
    ]),
    ("f05-rice-cooker-quinoa", [
        "What's the right water-to-quinoa ratio in a basic rice cooker?",
        "Mine came out mushy last time even at 1.5 to 1. Rinsing issue or ratio issue?",
    ]),
    ("f06-radiator-banging", [
        "The radiator in my apartment bangs like a hammer every morning when the heat kicks on. What causes that?",
        "The landlord says it's normal for old steam systems. Is there anything I can safely do myself?",
    ]),
    ("f07-field-trip-letter", [
        "Can you tighten this letter to parents? It's too long and a bit stiff.\n\n"
        """Dear Parents and Guardians,

I am writing to inform you that our Chemistry classes (Periods 2, 3 and 5) will be participating in a field trip to the Museum of Science and Industry on Thursday, May 14. This trip is designed to complement our current unit on energy and chemical reactions, and students will have the opportunity to attend a guided program in the museum's science lab as well as explore several of the permanent exhibits that relate to what we have been studying in class this semester.

Students will depart from the school by chartered bus at 8:15 a.m. and will return to school by approximately 2:30 p.m., which is before the end of the regular school day. Students who normally take the bus home will be able to do so as usual. Students should bring a packed lunch in a disposable bag, as there will not be an opportunity to purchase food at the museum due to the size of our group and time constraints. Students who receive school lunch may request a bag lunch from the cafeteria by speaking with the cafeteria staff no later than Tuesday, May 12.

The cost of the trip is $18 per student, which covers transportation and admission to the lab program. Please be aware that no student will be excluded from the trip for financial reasons; if the cost presents a hardship, please contact me directly and confidentially and we will make arrangements.

Please complete and sign the attached permission slip and return it, along with payment, by Friday, May 8. We are also looking for two parent chaperones. If you are interested in chaperoning, please indicate this on the permission slip. Chaperones must have completed the district volunteer background check.

If you have any questions or concerns, please do not hesitate to contact me by email or by calling the main office.

Sincerely,
Ms. Vega
Chemistry Department"""
        ,
        "Great. Can you also make a three-sentence version I can paste into the class messaging app?",
    ]),
    ("f08-commute-podcasts", [
        "I want a couple of podcasts for my commute, ideally science or history, episodes under 40 minutes. Ideas?",
        "Anything good for practicing listening in another language at a slower pace?",
    ]),
    ("f09-quiz-score-stats", [
        "Here are my quiz scores for one section. Can you give me the mean, median and standard deviation for each quiz and flag anyone whose scores dropped sharply?\n\n"
        """student_id,period,quiz1,quiz2,quiz3,quiz4
S1001,3,88,91,85,90
S1002,3,72,68,75,70
S1003,3,95,97,93,96
S1004,3,64,70,58,41
S1005,3,81,79,84,86
S1006,3,90,88,92,89
S1007,3,55,62,60,65
S1008,3,77,80,74,38
S1009,3,85,83,88,87
S1010,3,69,73,71,75
S1011,3,92,90,94,91
S1012,3,58,55,61,57
S1013,3,83,86,82,84
S1014,3,76,74,79,80
S1015,3,99,96,98,97
S1016,3,67,71,69,44
S1017,3,80,82,78,81
S1018,3,73,70,77,76
S1019,3,88,85,90,92
S1020,3,61,66,63,68
S1021,3,94,91,89,93
S1022,3,70,75,72,74
S1023,3,82,80,85,83
S1024,3,59,64,67,62
S1025,3,86,89,84,88
S1026,3,78,72,76,79
S1027,3,91,93,90,95
S1028,3,65,60,68,66
S1029,3,74,77,73,79
S1030,3,87,84,86,49
S1031,3,62,58,64,61
S1032,3,79,83,81,85
S1033,3,93,95,91,94
S1034,3,57,63,59,60
S1035,3,84,81,87,86
S1036,3,71,69,74,72
S1037,3,96,94,97,95
S1038,3,68,72,66,70
S1039,3,80,78,83,82
S1040,3,75,79,77,36
S1041,3,63,67,65,69
S1042,3,89,92,88,90"""
        "\n\nQuiz 4 was on limiting reagents.",
        "Can you give me the Excel formulas so I can do this myself for my other periods?",
    ]),
    ("f10-descale-kettle", [
        "My electric kettle has a crust of white scale inside. What's the best way to descale it?",
        "Citric acid or vinegar? I'd rather not have the kitchen smell like a salad.",
    ]),
    ("f11-le-chatelier-analogy", [
        "I need a good everyday analogy to explain Le Chatelier's principle to sophomores. The 'crowded room' one isn't landing.",
        "Can you turn that into three quick check-for-understanding questions?",
    ]),
    ("f12-band-setlist", [
        "Help me order our community band's setlist for the spring concert. Here's what we have, with keys, tempos and notes:\n\n"
        """1. My Girl - C major - 104 bpm - easy, crowd-pleaser, bass intro
2. What's Going On - E major - 102 bpm - mellow, sax solo
3. September - A major - 126 bpm - high energy, horns heavy
4. Ain't No Mountain High Enough - G major - 130 bpm - duet, needs two vocalists
5. Superstition - E-flat minor - 100 bpm - clav part on keys, tricky rhythm
6. Signed, Sealed, Delivered - F major - 114 bpm - upbeat
7. Let's Stay Together - F major - 100 bpm - ballad-ish, vocal showcase
8. Brick House - G major - 106 bpm - bass-driven, I need to rest hands before it
9. I Heard It Through the Grapevine - E-flat minor - 118 bpm - moody
10. Respect - C major - 115 bpm - big finish candidate
11. Isn't She Lovely - C-sharp minor - 120 bpm - harmonica part (we don't have one, keys covering)
12. Dancing in the Street - B-flat major - 118 bpm - singalong

Notes:
- Concert is about 60 minutes with a short break after song 6 or 7.
- Our drummer wants Superstition and Brick House not back to back.
- Two vocalists only available together for the second half.
- Horn section gets tired; don't put September and Respect next to each other.
- Keys player switches patches slowly, so avoid two keys-heavy songs in a row (Superstition, Isn't She Lovely).
- We want to open strong and end with a singalong or big finish."""
        "\n\nWhat order would you suggest?",
        "The vocalists just said they can only do the first half after all. How does that change it?",
    ]),
    ("f13-low-light-plants", [
        "My living room gets almost no direct sun. What houseplants will actually survive there?",
        "How often should I water a ZZ plant in a north-facing room?",
    ]),
    ("f14-knitting-abbreviations", [
        "I'm trying my first knitting pattern and it says 'k2tog, yo, ssk, p1' over and over. What does that mean?",
        "My ssk looks sloppy compared to the k2tog. Any tips?",
    ]),
    ("f15-dept-minutes", [
        "Can you summarize these science department meeting minutes into five bullet points for people who missed it?\n\n"
        """Science Department Meeting - Tuesday, May 12, 3:30 pm, Room 214
Present: Mr. Okafor (chair), Dr. Lindqvist, Ms. Reyes, Mr. Han, Ms. Vega, Ms. Dubois
Absent: Mr. Patel (coaching)

1. End-of-year inventory: Mr. Okafor reminded everyone that chemical and equipment inventory spreadsheets are due by the last day of classes. Expired reagents need to be listed separately for the hazardous waste pickup. Ms. Vega and Mr. Han will coordinate the chemistry stockroom; Dr. Lindqvist will do biology.

2. Budget: The department has roughly $2,300 left for this fiscal year. Priority requests: replacement goggles (sanitizer cabinet is failing), two new analytical balances, and probeware batteries. Ms. Reyes suggested holding $300 for emergencies. Vote: approved goggles and balances; probeware batteries to wait until July budget.

3. Curriculum: Physics wants to move the energy unit earlier next year so it lines up with chemistry thermochemistry. Ms. Dubois and Ms. Vega to meet and draft an aligned sequence by the June department meeting.

4. Safety: Two eyewash stations failed the weekly flush log check (Rooms 208 and 216). Facilities ticket submitted. Teachers reminded to log flushes every Monday.

5. Student teachers: Two student teachers placed with us next fall. Mentors needed; Mr. Han volunteered, one more mentor needed.

6. Misc: Retirement party for Ms. Dubois is being planned; contact Ms. Reyes if you want to contribute to the gift.

Next meeting: June, date TBD."""
        ,
        "Please add a short list of who owes what by when.",
    ]),
    ("f16-educator-expense", [
        "How does the educator expense deduction work? I spend a fair amount on classroom supplies every year.",
        "Do things like a document camera or lab goggles I bought for students count?",
    ]),
    ("f17-apps-script-error", [
        "My Google Apps Script for emailing missing-assignment reminders throws an error. Code and error:\n\n"
        """function sendMissingReminders() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName('Missing');
  const data = sheet.getRange(2, 1, sheet.getLastRow(), 5).getValues();
  // columns: A student name, B student email, C parent email, D assignment, E due date
  data.forEach(function(row) {
    const name = row[0];
    const studentEmail = row[1];
    const parentEmail = row[2];
    const assignment = row[3];
    const due = Utilities.formatDate(row[4], ss.getSpreadsheetTimeZone(), 'MMM d');
    const subject = 'Missing assignment: ' + assignment;
    const body = 'Hi ' + name + ', our records show that ' + assignment +
      ' (due ' + due + ') has not been turned in yet. Please submit it this week ' +
      'or come see me during office hours.';
    MailApp.sendEmail({
      to: studentEmail,
      cc: parentEmail,
      subject: subject,
      body: body
    });
  });
}

Error
Exception: Invalid argument: date (line 11, file "Code")
  at sendMissingReminders (Code:11:29)
  at Array.forEach (<anonymous>)
  at sendMissingReminders (Code:5:8)

Execution log
10:42:03 AM  Notice  Execution started
10:42:05 AM  Error   Exception: Invalid argument: date"""
        "\n\nThe first 20 or so emails actually went out before it crashed.",
        "Fixed, thanks. How do I stop it re-sending to the students who already got an email?",
    ]),
    ("f18-frittata", [
        "I want to make a vegetable frittata for dinner tonight with what I have: eggs, spinach, a red pepper, half an onion and some feta. How do I do it in a regular oven-safe skillet?",
        "How do I stop the bottom from overcooking before the top sets?",
    ]),
    ("f19-lower-back-stretches", [
        "My lower back is stiff from standing at the front of the classroom all day. Any simple stretches I can do in ten minutes in the evening?",
        "Is a standing mat worth it for teaching, or is it more about shoes?",
    ]),
    ("f20-sds-student-summary", [
        "Can you turn this SDS excerpt into a short, student-friendly safety card for our lab?\n\n"
        """SAFETY DATA SHEET - Hydrochloric Acid, 1.0 M solution

Section 2: Hazard Identification
Classification: Skin corrosion/irritation, Category 1B; Serious eye damage, Category 1; Specific target organ toxicity, single exposure (respiratory tract irritation), Category 3; Corrosive to metals, Category 1.
Signal word: Danger
Hazard statements: H290 May be corrosive to metals. H314 Causes severe skin burns and eye damage. H335 May cause respiratory irritation.
Precautionary statements: P260 Do not breathe mist/vapors/spray. P280 Wear protective gloves/protective clothing/eye protection/face protection. P301+P330+P331 IF SWALLOWED: Rinse mouth. Do NOT induce vomiting. P303+P361+P353 IF ON SKIN (or hair): Take off immediately all contaminated clothing. Rinse skin with water. P305+P351+P338 IF IN EYES: Rinse cautiously with water for several minutes. Remove contact lenses, if present and easy to do. Continue rinsing.

Section 4: First-Aid Measures
Eyes: Immediately flush with plenty of water for at least 15 minutes, occasionally lifting the upper and lower eyelids. Get medical attention.
Skin: Flush skin with plenty of water for at least 15 minutes while removing contaminated clothing and shoes.
Inhalation: Remove to fresh air. If breathing is difficult, give oxygen. Get medical attention.
Ingestion: Do not induce vomiting. Rinse mouth with water. Never give anything by mouth to an unconscious person.

Section 6: Accidental Release Measures
Neutralize spill with sodium bicarbonate or other suitable neutralizing agent. Absorb with inert material and place in a suitable container for disposal. Ventilate area.

Section 7: Handling and Storage
Store in a cool, dry, well-ventilated area in a corrosives cabinet. Keep away from incompatible materials: bases, metals, oxidizers. Always add acid to water, never water to acid."""
        "\n\nThey're 15-16, so keep it short and concrete.",
        "Can you add a two-line 'what to do if you spill it' box at the bottom?",
    ]),
    ("f21-phone-storage", [
        "My phone keeps saying storage is almost full. What usually takes up the most space and what's safe to delete?",
        "Messages is apparently using 9 GB. How do I clear old attachments without losing the conversations?",
    ]),
    ("f22-retirement-card-haiku", [
        "A colleague who taught physics for 31 years is retiring. Can you write a couple of haiku for her card, a little nerdy but warm?",
        "One more that mentions she always ran the egg-drop contest?",
    ]),
    ("f23-printer-offline", [
        "My wireless printer shows as 'offline' on my laptop even though it's on and printing a test page fine. What should I try?",
        "Would giving it a static IP on the router actually fix this for good?",
    ]),
    ("f24-stoichiometry-lesson", [
        "I need a 50-minute lesson plan introducing percent yield to 10th graders, with a hands-on piece that doesn't need a full lab setup.",
        "Can you add an exit ticket with three questions of increasing difficulty?",
    ]),
]

# Core sessions s01-s06 establish most facts; 12 fillers separate them from the
# test sessions s07-s08. s08 (Spanish) also establishes the visiting-hours fact,
# so 12 more fillers separate it from the remaining test sessions s09-s12.
SESSIONS_LONG = SESSIONS[:6] + FILLERS[:12] + SESSIONS[6:8] + FILLERS[12:] + SESSIONS[8:]
