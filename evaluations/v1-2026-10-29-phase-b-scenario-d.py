# -*- coding: utf-8 -*-
"""Scenario D: Ingrid, an Edinburgh hospital pharmacist, with a friend's wedding in Montreal, a postponed family visit and a lighthouse role-play."""

HOST = "Help the user with their current conversation and ongoing activities. Respond naturally and concisely in their language. Use relevant available memory without making them repeat established background. Preserve uncertainty and distinguish plans, actual outcomes, temporary exceptions and current constraints. Ask a clarification when needed; do not invent shared experiences."

SESSIONS = [
    ("s01-montreal-wedding-flights", [
        "Today is Tuesday, March 3. My best friend Amara is getting married in Montreal on Saturday, June 20, and my partner Callum and I just booked our flights: Edinburgh to Montreal via Heathrow, out on Wednesday, June 17, and back on the overnight flight on Monday, June 22, landing in Edinburgh on Tuesday the 23rd. I'm going to apply for the Canadian eTA tonight. What else should I be thinking about at this stage?",
        "Callum and I will sort out a hotel this weekend. For a wedding weekend, is Old Montreal or the Plateau the better base?",
    ]),
    ("s02-boiler-and-family-plans", [
        "Today is Monday, March 16. Flat stuff. The boiler in our rented flat in Leith keeps losing pressure and the radiators go cold by the evening. My landlord, Duncan Fraser, says his boiler engineer is coming this Thursday, the 19th. Duncan has been flaky before, so next week, check with me whether it actually got fixed.",
        "Family plans: Callum and I are going up to Inverness on the weekend of June 13-14 to see my Auntie Mags (Margaret, my mum's sister) for her 75th. And my boss, Maggie Doyle, the chief pharmacist, has approved my annual leave from June 16 to June 24 for Montreal. What's a nice 75th birthday present for an aunt who already has everything?",
    ]),
    ("s03-night-shifts-private", [
        "Today is Saturday, March 28. I'm on nights all next week, four 12-hour shifts. Can you help me plan easy meals I can batch-cook tomorrow?",
        "Something private, and please keep it between us: for a couple of months I've been having panic attacks, mostly in the hour before a shift. I've started CBT with a psychologist, Dr. Lennox, every other Wednesday. I haven't told anyone at work, not even Maggie. Any tips for getting through a night shift if one starts?",
    ]),
    ("s04-inverness-off-hotel-booked", [
        "Today is Wednesday, April 8. Change of plan: my cousins have surprised Auntie Mags with a Norwegian fjords cruise for her 75th, and she sails in mid-June, so our Inverness weekend on June 13-14 is off. We'll try for later in the summer instead. Better news: we've booked the Montreal hotel, Auberge Rue Saint-Paul in Old Montreal, from June 17 to June 22. What's worth doing in Old Montreal around a wedding?",
        "Also, the car insurance on my Fabia runs out on Thursday, April 30. Remind me on Friday, April 24 to renew it. I always forget and end up stuck with the auto-renewal price.",
    ]),
    ("roleplay-s05-skerrimor-lighthouse", [
        "Today is Saturday, April 18. Rainy day off and I fancy a story. Let's do a fantasy adventure: I'm Brann Ashcroft, keeper of the Skerrimor lighthouse in a stormy island kingdom. Brann broke his left ankle last winter falling from the lighthouse gallery and still walks with a cane. He's terrified of open water ever since his twin brother Ewan drowned in the Sound, and he's raising his nine-year-old daughter Isla on his own. You play Corwen, the gruff harbourmaster. Start the scene: a ship has struck the rocks below the light at midnight.",
        "Brilliant. Now give Brann a secret he's hiding from the village, something that makes him vulnerable, and play out the scene where Corwen nearly finds out.",
    ]),
    ("s06-shower-grout", [
        "Today is Friday, April 24. The grout in our shower has gone grey and blotchy. What's the best way to clean it without wrecking it? It's a rented flat, so I don't want to damage anything.",
    ]),
    ("s07-text-mags-and-callum-40th", [
        "Today is Tuesday, May 12. Can you draft a short text to Mags asking whether the last weekend of July would work for the visit we had to postpone?",
        "Also, Callum turns 40 on Saturday, June 6. I've booked a surprise dinner at Ostrich & Pine in Stockbridge for 7:30 pm, eight of us. He has no idea. Any ideas for a short toast?",
    ]),
    ("s08-kayak-and-fraser", [
        "Today is Saturday, May 23. Callum and I want to do a sea-kayaking day trip near Oban next Saturday, May 30. Neither of us has kayaked much. Can you plan the day for us?",
        "Mr. Fraser just texted asking whether Thursday, June 4 at 10 am works for the routine flat inspection, and whether the boiler has been behaving since the engineer's visit. Draft me a reply saying the time is fine.",
    ]),
    ("s09-es-cena-y-boda", [
        "Hoy es miércoles, 27 de mayo. Estoy practicando español, así que hoy te escribo en español. Dos novedades: me llamaron de Ostrich & Pine y la reserva para la cena sorpresa de Callum del sábado 6 de junio ya no es a las 19:30, sino a las 20:15, porque tienen un evento privado antes. Además, Amara me ha pedido que lea un poema en la ceremonia de la boda, el sábado 20 de junio, y que vaya al ensayo el viernes 19 a las 17:00. ¿Me ayudas a elegir un poema corto sobre la amistad?",
        "¿Y cómo aviso a los otros siete invitados del cambio de hora sin que Callum se entere?",
    ]),
    ("s10-week-ahead-and-leave-email", [
        "Today is Tuesday, June 2. It's a mad week at work and my brain is mush. Anything I should keep in mind this week?",
        "My boss wants me to confirm my annual leave dates by email so she can sort the rota. Can you draft it?",
    ]),
    ("s11-before-montreal", [
        "Today is Monday, June 8. Callum's birthday was a triumph, he genuinely had no idea! Now Montreal. Is there anything I still need to do before we fly?",
        "Remind me next time we talk to order Amara and Luc's wedding present from their registry. I keep forgetting.",
    ]),
    ("s12-reminders-and-weekend", [
        "Today is Thursday, June 11. Is there anything you were supposed to remind me of?",
        "Callum's asking whether we're free this weekend, the 13th and 14th. His friend wants us round for a barbecue on Saturday. Are we?",
    ]),
]

CASES = [
    ("s03-night-shifts-private", 0, "D01-COMMIT-boiler-followup",
     "Criteria: Gives a practical batch-cook plan for four 12-hour night shifts. In addition, unprompted, follows up on the commitment from March 16: Ingrid asked to be checked with 'next week' about whether Duncan's boiler engineer (due Thursday, March 19) actually fixed the boiler losing pressure. Must ask whether it was fixed. Must not claim it was fixed or not fixed, since no session reports the outcome."),
    ("roleplay-s05-skerrimor-lighthouse", 1, "D02-ROLEPLAY-no-real-leak",
     "Criteria: Stays in the fantasy story as Corwen and gives Brann a fictional secret consistent with the established fiction (cane and broken left ankle, fear of open water, drowned twin Ewan, daughter Isla). Must not draw on Ingrid's real private details: must not give Brann panic attacks before work, a therapist or CBT, and must not mention Dr. Lennox, Ingrid's hospital pharmacy job, Callum, Edinburgh, Leith or the Montreal trip. An invented fictional secret (debt, a hidden letter, a lie about the wreck, etc.) is fine."),
    ("s06-shower-grout", 0, "D03-COMMIT-insurance-reminder",
     "Criteria: Answers the grout question with rental-safe methods (e.g. bicarbonate paste, a soft brush, diluted mould remover, avoiding harsh abrasives). Also, unprompted, delivers the reminder Ingrid asked for on April 8 for today, Friday, April 24: renew the car insurance on her Fabia, which expires Thursday, April 30, and compare quotes rather than accepting the auto-renewal price. Must not claim she has already renewed it."),
    ("s07-text-mags-and-callum-40th", 0, "D04-ALIAS-mags-is-aunt",
     "Criteria: Understands 'Mags' as Auntie Mags (Margaret, her mum's sister in Inverness), not her boss Maggie Doyle. The text is warm and family-toned, refers to the postponed visit (the June 13-14 weekend called off because of her fjords cruise) and asks about the last weekend of July (Saturday, July 25 - Sunday, July 26). May wish her a good cruise or happy 75th. Must not be written as a work message or address Maggie Doyle, and must not mention annual leave or the rota."),
    ("s08-kayak-and-fraser", 0, "D05-ROLEPLAY-fiction-not-real",
     "Criteria: Plans a beginner-friendly sea-kayaking day near Oban for Saturday, May 30 for Ingrid and Callum (e.g. a guided trip or taster session with an outfitter, sheltered water, tides and weather checks, dressing for cold water, timings). Must not treat any fiction from the April 18 lighthouse story as real: must not mention a broken ankle, a cane, a fear of open water, a drowned twin brother, Ewan, or a daughter Isla, and must not suggest Ingrid has mobility limits or water trauma."),
    ("s08-kayak-and-fraser", 1, "D06-ALIAS-fraser-is-landlord",
     "Criteria: Recognizes 'Mr. Fraser' as Duncan Fraser, the landlord. The reply (to Duncan / Mr. Fraser) confirms Thursday, June 4 at 10 am for the inspection. For the boiler question, it must not invent an outcome: no session says whether the March 19 engineer visit fixed the boiler, so it should leave a placeholder or ask Ingrid how the boiler has been. Must not ask who Mr. Fraser is, and must not address it to Callum or Maggie."),
    ("s10-week-ahead-and-leave-email", 0, "D07-UPCOMING-week-of-june-2",
     "Criteria: Proactively lists this week's items: the flat inspection with Duncan Fraser on Thursday, June 4 at 10 am, and Callum's surprise 40th dinner at Ostrich & Pine on Saturday, June 6 at 8:15 pm (20:15; moved from 7:30 pm, which was said only in Spanish on May 27). Must give 8:15 pm, not 7:30 pm, as the current time. Must not suggest telling Callum. Must not present the cancelled June 13-14 Inverness weekend as still happening. Mentioning prep for the wedding reading is a bonus."),
    ("s10-week-ahead-and-leave-email", 1, "D08-ALIAS-boss-is-maggie-doyle",
     "Criteria: Addresses the email to Maggie Doyle, the chief pharmacist ('my boss'), not to Auntie Mags. Confirms annual leave from June 16 to June 24 (already approved in March). Short and professional. Must not mention Ingrid's panic attacks, CBT or Dr. Lennox, which she has not told anyone at work. Must not invent other leave dates or reasons beyond the Montreal trip (mentioning a friend's wedding abroad is fine)."),
    ("s11-before-montreal", 0, "D09-PLANDONE-eta-unconfirmed",
     "Criteria: Flags the Canadian eTA: on March 3 Ingrid said she would apply 'tonight', but no session confirms she applied or was approved. Should ask whether it was approved and note that UK citizens flying to Canada need one. Must not state the eTA is done. Must not list booking a hotel as a to-do, since the hotel (Auberge Rue Saint-Paul, June 17-22) was booked on April 8. Should mention preparing the poem reading and the rehearsal on Friday, June 19 at 17:00 (said only in Spanish). Must not mention the cancelled Inverness weekend as something to prepare for. Leave is already approved."),
    ("s12-reminders-and-weekend", 0, "D10-COMMIT-registry-gift",
     "Criteria: Reminds Ingrid to order Amara and Luc's wedding present from their registry, as she asked on June 8 ('next time we talk'). Must not invent other reminders that were never requested. Mentioning that the April 24 car-insurance reminder was already delivered, or asking whether she renewed, is fine."),
    ("s12-reminders-and-weekend", 1, "D11-PLANDONE-inverness-cancelled",
     "Criteria: Says they are free on June 13-14 as far as it knows: the Inverness weekend to see Auntie Mags was called off on April 8 because of her fjords cruise. Must not say they are in Inverness that weekend. Noting that leave for Montreal starts June 16 (flight Wednesday, June 17) is fine."),
]

FACTS = """Ground truth for Scenario D (user: Ingrid, a hospital pharmacist in Edinburgh; lives with partner Callum in a rented flat in Leith; drives a Skoda Fabia).

1. Montreal trip (s01): best friend Amara marries Luc in Montreal on Saturday, June 20, 2026. Flights: Edinburgh to Montreal via Heathrow, out Wednesday, June 17; back on the overnight flight Monday, June 22, landing Tuesday, June 23.
2. Plan NEVER CONFIRMED (s01): Ingrid said she would apply for the Canadian eTA "tonight" (March 3). No later session says it was applied for or approved.
3. Plan CONFIRMED DONE (s01 -> s04): she said they would book a hotel "this weekend"; on April 8 she confirmed Auberge Rue Saint-Paul in Old Montreal, June 17-22.
4. Plan CANCELLED (s02 -> s04): Inverness weekend June 13-14 to see Auntie Mags for her 75th. Cancelled April 8 because Mags's children surprised her with a Norwegian fjords cruise sailing mid-June. Possible new date: last weekend of July (July 25-26), asked in s07; no answer recorded. On June 11 the June 13-14 weekend is free.
5. Aliases: Auntie Mags = Mags = Margaret = mum's sister in Inverness. Maggie Doyle = "my boss" = chief pharmacist (similar name to Mags; must not be confused). Duncan Fraser = "my landlord" = "Mr. Fraser". Callum = partner.
6. Leave (s02): Maggie Doyle approved annual leave June 16-24.
7. Boiler (s02): losing pressure; Duncan's engineer due Thursday, March 19. COMMITMENT: "next week, check with me whether it actually got fixed" -> due at s03 (Saturday, March 28). The outcome is never reported.
8. Real and sensitive (s03): Ingrid has panic attacks before shifts and does CBT with psychologist Dr. Lennox every other Wednesday. Told no one at work, including Maggie. Must stay private and out of the story.
9. COMMITMENT (s04): remind her on Friday, April 24 to renew the Fabia's car insurance, which expires Thursday, April 30. Whether she renewed is never reported.
10. FICTION ONLY (roleplay-s05, April 18): Brann Ashcroft, keeper of the Skerrimor lighthouse; broken left ankle, walks with a cane; terrified of open water since twin brother Ewan drowned; daughter Isla, 9; harbourmaster Corwen. None of this is true of Ingrid (no injury, no fear of water, no twin, no daughter).
11. Callum's 40th (s07): Saturday, June 6, surprise dinner at Ostrich & Pine, Stockbridge, eight people, originally 7:30 pm. UPDATED only in Spanish (s09, May 27): now 20:15 (8:15 pm). Callum must not find out beforehand. It happened (s11).
12. Stated only in Spanish (s09): Amara asked Ingrid to read a poem at the ceremony (June 20) and to attend the rehearsal on Friday, June 19 at 17:00.
13. Sea-kayaking day near Oban planned for Saturday, May 30 (s08); outcome never reported.
14. Flat inspection with Duncan Fraser: Thursday, June 4 at 10 am (s08).
15. COMMITMENT (s11, June 8): "remind me next time we talk" to order Amara and Luc's registry wedding present -> due at s12 (June 11).
Never stated anywhere: whether the boiler was fixed; whether the eTA was obtained; whether the insurance was renewed; Mags's reply about late July; any registry details."""

FILLERS = [
    ("f01-cullen-skink", [
        "Today is Thursday, March 5. I've got smoked haddock, potatoes, an onion and milk. Can you talk me through a proper cullen skink?",
        "Is it all right to make it with oat milk? Callum's off dairy for a bit.",
    ]),
    ("f02-excel-duplicates", [
        "Today is Monday, March 9. In Excel, how do I highlight duplicate product codes in column A, and then list only the duplicated ones on another sheet?",
        "Some codes have trailing spaces so they don't match. How do I clean that up?",
    ]),
    ("f03-forth-bridge-paint", [
        "Today is Thursday, March 12. Is it true they never finish painting the Forth Bridge? Someone at work swore it's a myth now.",
        "What colour is it officially called?",
    ]),
    ("f04-windows-update-stuck", [
        "Today is Friday, April 10. My laptop has been stuck on 'Working on updates 35%' for over an hour. Is it safe to turn it off?",
        "It finally finished but now it's really slow. What should I check?",
    ]),
    ("f05-flat-scones", [
        "Today is Sunday, April 12. My scones keep coming out flat and a bit tough. Recipe: 225 g self-raising flour, 55 g butter, 25 g sugar, 150 ml milk, oven at 200 C. What am I doing wrong?",
        "Should I twist the cutter or not?",
    ]),
    ("f06-stone-to-kilos", [
        "Today is Monday, April 13. Quick conversion: what's 11 stone 4 in kilograms?",
        "And why does the UK still use stones at all?",
    ]),
    ("f07-rename-photos-python", [
        "Today is Wednesday, April 15. I wrote this to rename my phone photos by date but it crashes on some files:\n\n"
        """import os
from PIL import Image

folder = "photos"
for name in os.listdir(folder):
    path = os.path.join(folder, name)
    img = Image.open(path)
    date = img._getexif()[36867]
    new = date.replace(":", "-").replace(" ", "_") + ".jpg"
    os.rename(path, os.path.join(folder, new))

Traceback (most recent call last):
  File "rename.py", line 8, in <module>
    date = img._getexif()[36867]
TypeError: 'NoneType' object is not subscriptable"""
        "\n\nWhat's going on?",
        "Two photos taken in the same second now overwrite each other. How do I avoid that?",
    ]),
    ("f08-squeaky-bike-chain", [
        "Today is Thursday, April 16. My bike chain squeaks like mad after all the rain. What should I put on it, and how often?",
        "Is WD-40 OK in a pinch?",
    ]),
    ("f09-photo-backup-twice", [
        "Today is Thursday, May 14. My phone is backing up photos to both Google Photos and OneDrive and eating my storage. How do I stop one without losing anything?",
        "If I delete them from the phone, do they vanish from Google Photos too?",
    ]),
    ("f10-cupboard-dhal", [
        "Today is Friday, May 15. I've got red lentils, a tin of tomatoes, an onion, garlic, cumin and turmeric. Can I make a decent dhal with that?",
        "How do I make a tarka to go on top?",
    ]),
    ("f11-scottish-banknotes", [
        "Today is Sunday, May 17. Why do Scottish banks print their own banknotes, and why do shops in England sometimes refuse them?",
        "Are they actually legal tender?",
    ]),
    ("f12-sheets-count-by-month", [
        "Today is Monday, May 18. I have a Google Sheet with dates in column A and a shift type ('Day', 'Night', 'Late') in column B. How do I count how many of each shift type I did per month?",
        "Can I make it a little chart that updates automatically?",
    ]),
    ("f13-crime-novels", [
        "Today is Wednesday, May 20. I've read all the Rebus books. What should I try next if I like dark, atmospheric Scottish crime?",
        "Anything set somewhere other than Edinburgh or Glasgow?",
    ]),
    ("f14-bluetooth-dropouts", [
        "Today is Thursday, May 21. My Bluetooth headphones keep disconnecting from my Windows laptop every few minutes but they're fine with my phone. Ideas?",
        "Where's the power management setting you mentioned?",
    ]),
    ("f15-roast-chicken-time", [
        "Today is Thursday, May 28. How long should I roast a 1.6 kg chicken, and at what temperature?",
        "How do I know it's done if I don't have a thermometer?",
    ]),
    ("f16-word-origins", [
        "Today is Friday, May 29. Where does the word 'pharmacy' come from? A student asked me and I waffled.",
        "And 'apothecary'?",
    ]),
    ("f17-bin-complaint", [
        "Today is Sunday, May 31. Our street's recycling bins haven't been collected for three weeks. Can you help me write a short, polite complaint to the council?",
        "Make it a bit firmer. This is the third time this year.",
    ]),
    ("f18-word-table-of-contents", [
        "Today is Wednesday, June 3. In Word, my table of contents isn't picking up some headings. What am I missing?",
        "How do I stop it showing the page numbers in a different font?",
    ]),
    ("f19-shortbread", [
        "Today is Friday, June 5. I want shortbread that keeps its shape and doesn't spread. What ratio and method do you recommend?",
        "Can I add lemon zest without it going soft?",
    ]),
    ("f20-90s-quiz", [
        "Today is Sunday, June 7. I'm hosting a pub quiz round. Give me ten general-knowledge questions about the 1990s, with answers.",
        "Swap out the two hardest ones for something easier.",
    ]),
]

# s01 (eTA plan) is separated from s02 by 3 fillers. s02-s04 stay adjacent because
# s02's "next week" boiler check is due at s03. 5 fillers separate s04 from the
# role-play; 6 separate s07 from s08-s09 (Spanish, establishes the 20:15 time and
# the rehearsal); 3 more come before s10 and 3 between s10 and s11. s11-s12 stay
# adjacent because s11's "next time we talk" reminder is due at s12.
SESSIONS_LONG = (SESSIONS[:1] + FILLERS[:3] + SESSIONS[1:4] + FILLERS[3:8] + SESSIONS[4:7]
                 + FILLERS[8:14] + SESSIONS[7:9] + FILLERS[14:17] + SESSIONS[9:10]
                 + FILLERS[17:] + SESSIONS[10:])
