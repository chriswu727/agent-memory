"""Long variant: the same ten sessions with 24 unrelated sessions interleaved, so early facts age out of any recent window."""
from scenario import CASES, HOST, SESSIONS  # noqa: F401

FILLERS = [
    ("f01-python-csv", ["I have a Python script that reads a 2 GB CSV of sensor readings with pandas and it runs out of memory on my laptop. The columns are timestamp, sensor_id, temperature and humidity. What is the simplest way to compute daily averages per sensor without loading everything at once?",
                        "Chunks make sense. Can you show the version that writes the results to a new CSV instead of printing them?"]),
    ("f02-sourdough", ["My sourdough starter smells like nail polish remover and barely rises anymore. I feed it once a day with equal weights of flour and water and keep it on the kitchen counter, which is around 24 degrees. What is going wrong?",
                       "Okay, I'll try twice-daily feeds. How long before I can bake with it again?"]),
    ("f03-flat-move", ["We are moving flats at the end of next month, from a one-bedroom to a two-bedroom across town. I want a checklist for the last four weeks: utilities, address changes, packing order, that kind of thing.",
                       "Can you turn week three into a day-by-day list?"]),
    ("f04-sql-join", ["At work I have two tables, orders(order_id, customer_id, created_at, total) and customers(customer_id, region). I need monthly revenue per region for 2026, including regions with zero orders in a month. Can you write the SQL for Postgres?",
                      "Great. How would I add a column with the month-over-month percentage change?"]),
    ("f05-houseplants", ["My monstera has yellow leaves at the bottom and brown crispy edges on the newer ones. It's two metres from a north-facing window and I water it every Sunday. Any idea what's wrong?",
                         "Should I repot it now or wait until spring?"]),
    ("f06-git-rebase", ["I accidentally committed a 300 MB video file three commits ago on a feature branch and now GitHub rejects the push. Nothing has been pushed yet. How do I remove it from history safely?",
                        "Thanks. And how do I stop this happening again for video files?"]),
    ("f07-budget-sheet", ["I want a simple monthly budget spreadsheet. Income is about 4,200 after tax; rent is 1,450; I'd like to save 15 percent. Can you suggest categories and formulas I can paste into Google Sheets?",
                          "Add a column that turns red when a category goes over budget."]),
    ("f08-photo-trip", ["A friend and I are planning a long weekend in the Lake District in spring, mostly for photography at sunrise and sunset. We'll have a car. Which areas and viewpoints would you suggest for three days?",
                        "What lenses should I bring if I can only carry two?"]),
    ("f09-email-tone", ["Can you make this message to a supplier sound firmer but still polite: 'Hi, just checking if there's any update on the delayed shipment, no worries if not, thanks!'",
                        "Shorter, please, and mention that we need it by the 20th."]),
    ("f10-regex", ["I need a regular expression that matches UK postcodes in free text, like SW1A 1AA or M1 1AE, but not random strings. Can you explain each part too?",
                   "Can you give a few test strings that should and should not match?"]),
    ("f11-board-games", ["I'm hosting a board game night for six adults who mostly haven't played modern games. We have about three hours. What games would work, and in what order?",
                         "One person is colour-blind. Any of those a problem?"]),
    ("f12-bike-repair", ["The rear derailleur on my commuter bike skips gears under load, especially on the middle cogs. I replaced the chain last month. What should I check first?",
                         "How do I adjust the cable tension with the barrel adjuster, step by step?"]),
    ("f13-docker", ["My docker-compose setup has a web app and a Postgres container. The app starts before the database is ready and crashes. What is the cleanest fix?",
                    "Show me the healthcheck version of the compose file."]),
    ("f14-gift-ideas", ["My dad is turning 65 and loves gardening, history podcasts and old maps. Budget about 80. Any gift ideas that aren't generic?",
                        "He already has a lot of books. What about an experience instead?"]),
    ("f15-spanish", ["I'm learning Spanish on and off. I can order food and ask directions but freeze in real conversations. What routine would help in 20 minutes a day?",
                     "Recommend a podcast for that level."]),
    ("f16-meeting-notes", ["Please turn these notes into a tidy summary with action items: 'vendor portal launch slips to Nov, Daniel wants risks list by Fri, Tom owns data migration test, need legal review of new T&Cs, next sync Tuesday'.",
                           "Make the action items a table with owner and due date."]),
    ("f17-coffee", ["I bought a hand grinder and a V60. My coffee tastes sour and thin. I'm using 15 g coffee to 250 g water just off the boil. What should I change first?",
                    "How fine is 'finer' in practice?"]),
    ("f18-js-debounce", ["In my React search box, every keystroke triggers an API call. How do I debounce it properly with hooks, and cancel stale requests?",
                         "Can you add a loading state to that example?"]),
    ("f19-car-insurance", ["My car insurance renewal went up 40 percent with no claims. What questions should I ask before switching, and what usually lowers the price?",
                           "Is it worth adding a named driver?"]),
    ("f20-kids-party", ["My nephew turns seven next month and I offered to plan games for ten kids in a small garden for two hours. Ideas that don't need much equipment?",
                        "What if it rains?"]),
    ("f21-linux-disk", ["My Ubuntu server's root disk is 95 percent full. How do I find what's taking space and clean it up safely?",
                        "Journal logs are 6 GB. How do I cap them permanently?"]),
    ("f22-book-club", ["Our book club wants something under 300 pages that sparks discussion, not too bleak. Six people, mixed tastes. Suggestions?",
                       "Give me three discussion questions for your first pick."]),
    ("f23-negotiation", ["I'm asking for a raise next week. I've taken on the vendor portal project and mentored two juniors this year. How should I structure the conversation?",
                         "What if my manager says the budget is frozen?"]),
    ("f24-weekend-cleanup", ["I have one free Saturday to sort out the flat after the move: unpacked boxes, a broken shelf, and paperwork. Help me plan the day so it actually gets done.",
                             "Make it a bit less ambitious; I also want an afternoon nap."]),
]

_core = dict(SESSIONS)
_fill = iter(FILLERS)


def _take(count):
    return [next(_fill) for _ in range(count)]


ORDER = [*(s for s in SESSIONS[:5]), *_take(16), ("s06-zh-fuelling", _core["s06-zh-fuelling"]), *_take(2),
         ("s07-knee", _core["s07-knee"]), *_take(2), ("s08-toast-check", _core["s08-toast-check"]), *_take(2),
         ("s09-race-week", _core["s09-race-week"]), *_take(2), ("s10-after", _core["s10-after"])]
SESSIONS_LONG = ORDER


def _pasted(kind):
    """Realistic pasted material (code, logs, notes) that makes the history exceed an injection budget."""
    if kind == "csv":
        rows = [f"2026-09-{d:02d}T{h:02d}:00:00Z,sensor-{s},{18 + (d + s) % 9}.{h % 10},{40 + (h * s) % 30}"
                for d in range(1, 4) for h in range(0, 24, 3) for s in range(1, 3)]
        return "Here is a sample:\ntimestamp,sensor_id,temperature,humidity\n" + "\n".join(rows)
    if kind == "sql":
        return ("Schema:\nCREATE TABLE orders (order_id bigint PRIMARY KEY, customer_id bigint NOT NULL, created_at timestamptz NOT NULL, total numeric(12,2) NOT NULL);\n"
                "CREATE TABLE customers (customer_id bigint PRIMARY KEY, region text NOT NULL, signup_at timestamptz);\n"
                + "\n".join(f"INSERT INTO customers VALUES ({i}, '{['north', 'south', 'east', 'west'][i % 4]}', now());" for i in range(1, 25)))
    if kind == "git":
        return "git log --oneline:\n" + "\n".join(f"{(0xa1b2c3 + i * 7919) % 0xffffff:06x} {msg}" for i, msg in enumerate(
            ["add product video", "update landing copy", "fix navbar overflow", "bump dependencies", "add analytics consent",
             "refactor price table", "add FAQ section", "tweak hero spacing", "add testimonials", "initial landing page"] * 3))
    if kind == "compose":
        return ("docker-compose.yml:\nservices:\n  web:\n    build: .\n    ports: ['8000:8000']\n    environment:\n      DATABASE_URL: postgres://app:app@db:5432/app\n"
                "    depends_on: [db]\n  db:\n    image: postgres:16\n    environment:\n      POSTGRES_USER: app\n      POSTGRES_PASSWORD: app\n"
                "    volumes: ['pgdata:/var/lib/postgresql/data']\nvolumes:\n  pgdata: {}\n\nLogs:\n" + "\n".join(
                    f"web-1  | {i:02d}:00 psycopg.OperationalError: connection to server at \"db\" failed: Connection refused" for i in range(18)))
    if kind == "notes":
        return "Full notes:\n" + "\n".join(f"- {line}" for line in [
            "vendor portal launch slips to Nov because SSO integration is late", "Daniel wants a risks list by Friday",
            "Tom owns the data migration dry run", "legal must review the new terms before go-live", "next sync Tuesday 10:00",
            "support team needs a training session", "staging environment is unstable since the upgrade",
            "budget for contractors is approved until December", "marketing wants screenshots by mid-November",
            "accessibility audit scheduled for week 46"] * 3)
    if kind == "react":
        return ("Current component:\n" + "\n".join([
            "import { useEffect, useState } from 'react';", "export function Search() {", "  const [q, setQ] = useState('');",
            "  const [results, setResults] = useState([]);", "  useEffect(() => {", "    fetch(`/api/search?q=${q}`)",
            "      .then(r => r.json()).then(setResults);", "  }, [q]);", "  return (", "    <div>",
            "      <input value={q} onChange={e => setQ(e.target.value)} />",
            "      <ul>{results.map(r => <li key={r.id}>{r.title}</li>)}</ul>", "    </div>", "  );", "}"]) * 2)
    if kind == "du":
        return "du -h --max-depth=1 /var | sort -h:\n" + "\n".join(
            f"{size}\t/var/{name}" for size, name in [("4.0K", "opt"), ("12K", "mail"), ("1.2M", "backups"), ("88M", "spool"),
                                                     ("310M", "cache"), ("2.1G", "www"), ("6.0G", "log"), ("11G", "lib")] * 3)
    return "Achievements this year:\n" + "\n".join(f"- {item}" for item in [
        "led the vendor portal project from scoping to beta", "mentored two junior analysts through their first quarter",
        "automated the monthly vendor report, saving about six hours a month", "presented the Q2 results to the leadership team",
        "cut onboarding time for new suppliers from three weeks to one", "ran the data quality working group"] * 3)


_PASTES = {"f01-python-csv": "csv", "f04-sql-join": "sql", "f06-git-rebase": "git", "f13-docker": "compose",
           "f16-meeting-notes": "notes", "f18-js-debounce": "react", "f21-linux-disk": "du", "f23-negotiation": "wins"}
SESSIONS_LONG = [(name, [turns[0] + "\n\n" + _pasted(_PASTES[name]), *turns[1:]] if name in _PASTES else turns)
                 for name, turns in ORDER]
