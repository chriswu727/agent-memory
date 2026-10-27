# -*- coding: utf-8 -*-
"""Scenario B: Hao, a Seattle data engineer, hosting his mother and renovating his kitchen."""

HOST = "Help the user with their current conversation and ongoing activities. Respond naturally and concisely in their language. Use relevant available memory without making them repeat established background. Preserve uncertainty and distinguish plans, actual outcomes, temporary exceptions and current constraints. Ask a clarification when needed; do not invent shared experiences."

SESSIONS = [
    ("s01-mom-visit-prep", [
        "Today is Thursday, March 19. My mom is flying in from Chengdu to stay with us for about ten weeks. She lands at Sea-Tac on Thursday, April 16 and flies home June 25. She had a hip replacement last year and uses a walker now. She really can't do stairs, and after about 15 minutes on her feet she has to sit down. She's also type 2 diabetic. She speaks Mandarin and Sichuanese, basically no English. We're in a two-story townhouse in Seattle and the guest room is upstairs. What should I sort out before she gets here?",
        "Good point about the stairs. We have a den on the ground floor right next to the half bath, so I'll turn that into her room. One more thing about how I like to work with you: when you draft texts or messages for me, keep them short. I hate long texts. And sign them just \"Hao\".",
    ]),
    ("s02-kitchen-reno", [
        "Today is Monday, March 23. We're finally redoing the kitchen. The contractor is Kevin Lu from Lu Build and his quote is $18,400. Demo starts Monday, April 6, and he says the kitchen will be out of commission until about Friday, April 17. My hard ceiling for the whole project is $24,000 all-in, including anything I pay other trades directly. Is it sensible to hold back 10% of the quote as contingency?",
        "Also, totally separate: I still need to buy visitor medical insurance for my mom before she flies. I'm leaning toward one of those plans with $100k of coverage. I'll sort it out this weekend.",
    ]),
    ("s03-change-order", [
        "Today is Thursday, April 2. Two kitchen updates. Kevin is sending a change order for $2,900 to move the sink into the island. And I've hired a separate electrician, Kelvin Tse from Brightline Electric, to do the under-cabinet lighting and move the outlet by the window. He bills me directly: $1,650. Where does that leave me against my ceiling?",
        "OK. Draft a text to Kevin saying I approve the $2,900 change order.",
    ]),
    ("s04-no-kitchen", [
        "Today is Wednesday, April 8. The kitchen is completely gutted. Until the crew finishes (around the 17th) all we have is a microwave, a rice cooker and an induction hot plate set up in the garage, plus the laundry sink for washing up. Give me three weeknight dinners for me, my wife Jess and our 8-year-old Leo that work with that setup.",
    ]),
    ("s05-birthday-plan", [
        "Today is Saturday, April 18. Mom landed safely on Thursday and she's settling into the den. Her 70th birthday is Saturday, May 9, and I've booked the private room at Harbor Pearl in Bellevue for that evening: 6:30 pm, 12 people. My sister Mei is coming down from Vancouver with her husband and their two kids. Can you suggest a rough flow for the evening? Toasts, cake, that kind of thing.",
        "In our family you always have longevity noodles on your birthday. Any way to make that work given her blood sugar?",
    ]),
    ("s06-dinner-moved", [
        "Today is Tuesday, April 21. Kitchen news first: Kevin's crew wrapped up yesterday, so we have a working kitchen again. Only the backsplash tile is still to come. Bad news on the birthday though. I went to look at Harbor Pearl's private room and it's up a full flight of stairs with no elevator, so that's out for Mom. I cancelled and rebooked at Jade Garden in the International District. It's on the ground floor with a step-free entrance, but the only evening they had the room was Sunday, May 17 at 6 pm. Same 12 people. Write the message for the family group chat about the change.",
    ]),
    ("s07-zh-pike-place", [
        "今天是4月26日，星期天。下周六（5月2日）我想带我妈去 Pike Place Market 逛逛，然后去海边走走。她一直念叨想去看看。你帮我把那天的路线和时间安排一下吧。",
        "还有件事。周五晚上她吃了几个虾饺，没过多久脸上和手臂就起了一片红疹，吃了抗过敏药才退下去。我表妹在这边当护士，看了照片说很像贝类过敏，让她以后虾、蟹、龙虾这些都别碰，等回成都再去做过敏测试。帮我用中文写几句简单的话贴在冰箱上，提醒她哪些东西不能吃。",
    ]),
    ("s08-waterfall-edge", [
        "Today is Thursday, April 30. Kevin came by with quartz samples and says a waterfall edge on the island would be another $1,200. Jess loves it. Do we have room for it?",
    ]),
    ("s09-outlet-and-cooking", [
        "Today is Tuesday, May 5. The sparky still hasn't come back to move the outlet by the window, and it's been two weeks. Can you write me something to chase him?",
        "Mom wants to cook dinner for everyone on Sunday, a couple of the Sichuan dishes she always makes. Anything I should set up so she can do it comfortably?",
        "Oh, and Lu Build just texted asking if Thursday at 4 works for the final walkthrough. Reply yes for me.",
    ]),
    ("s10-banquet-menu", [
        "Today is Friday, May 8. The restaurant wants us to choose a set menu for the 17th. Their banquet option is salt-and-pepper prawns, crab with ginger and scallion, steamed whole fish, gai lan with garlic, longevity noodles, and mango pudding for dessert. Anything I should change?",
        "My sister is trying to decide whether to drive down or take the train for it. She'll have the two kids with her. What would you tell her?",
    ]),
    ("s11-quick-questions", [
        "Today is Monday, May 11. Quick one, unrelated to everything else: give me a bash one-liner that finds files over 500 MB under my home directory and lists them biggest first.",
        "Thanks. Different thing: what did Kevin say the warranty on the cabinets was? I want to put it in my notes.",
    ]),
    ("s12-urgent-care", [
        "Today is Wednesday, May 13. Mom slipped on a wet ramp at the park and her wrist is swelling up. We're heading to urgent care now. Her travel insurance should cover this, right? What do I need to bring?",
        "What should I tell the front desk when we check in?",
        "Also, my aunt in Chengdu just messaged asking which day Mom's birthday dinner is so she can video call in. Remind me?",
    ]),
]

CASES = [
    ("s07-zh-pike-place", 0, "C01-zh-outing-mobility",
     "Criteria: Replies in Chinese. The plan must account for Mom's mobility limits without Hao restating them: she uses a walker, cannot manage stairs, and needs to sit after about 15 minutes on her feet. Expect things like step-free routes and elevators instead of the market's stairs or the Hillclimb, frequent seated breaks, short walking segments, drop-off close to the entrance, and avoiding peak crowds. Diabetes-aware snack or meal suggestions are a bonus. Must not plan long continuous walking or routes that depend on stairs, and must not suggest Hao or Mom have been there together before."),
    ("s08-waterfall-edge", 0, "C02-budget-headroom",
     "Criteria: Uses the remembered numbers: $24,000 all-in ceiling; committed $18,400 (Kevin's quote) + $2,900 (sink change order) + $1,650 (Kelvin, electrician) = $22,950, leaving $1,050. Must say that the $1,200 waterfall edge does not fit (it would bring the total to $24,150, $150 over), or otherwise clearly show it breaks the ceiling. Suggesting negotiation, a trade-off or a cut elsewhere is fine. Must not say it fits comfortably, and must not ask Hao to restate the budget or existing costs."),
    ("s09-outlet-and-cooking", 0, "C03-sparky-is-kelvin",
     "Criteria: Understands 'the sparky' as the electrician, Kelvin Tse of Brightline Electric, who was hired to move the outlet by the window. The draft is addressed to Kelvin, not to Kevin or Lu Build. The draft is short (a few sentences) and signed 'Hao', following the preference Hao stated once in the first session. Must not invent a previously agreed date for the work."),
    ("s09-outlet-and-cooking", 1, "C04-kitchen-back-mobility",
     "Criteria: Treats the kitchen as usable. The renovation finished around April 20, and the microwave and garage hot-plate setup was only temporary. Must not say the kitchen is unavailable or suggest cooking in the garage. Should adapt to Mom's lasting limits: walker, can only stand about 15 minutes. Expect things like a stool or seated prep, keeping items within reach, a clear path for the walker, and others doing the lifting and carrying. Bonus if it avoids shellfish."),
    ("s09-outlet-and-cooking", 2, "C05-walkthrough-is-kevin",
     "Criteria: Recognizes Lu Build as Kevin's company, so the reply goes to Kevin (or to Lu Build without a name) and not to Kelvin. Confirms Thursday at 4 pm (Thursday, May 7) for the final walkthrough. The reply is short and signed 'Hao'."),
    ("s10-banquet-menu", 0, "C06-menu-shellfish-diabetes",
     "Criteria: Flags the salt-and-pepper prawns and the crab because Mom likely has a shellfish allergy. This was said only in Chinese on April 26: avoid shrimp, crab and lobster. Suggests replacing those dishes. Should also mention the diabetes angle, for example the mango pudding's sugar or the noodle portion, while keeping the longevity noodles tradition if possible. Must not approve the menu unchanged. Must not confuse the venue or date (Jade Garden, Sunday, May 17)."),
    ("s10-banquet-menu", 1, "C07-sister-from-vancouver",
     "Criteria: Knows 'my sister' is Mei, coming from Vancouver, BC, with her husband and two kids, so the trip crosses the US-Canada border. Mentions border documents for the kids and/or border wait times. Compares driving (about 3 hours plus the border) with the Amtrak Cascades train (about 4 hours) for a Sunday 6 pm dinner on May 17. Must not treat her as local or ask where she is coming from."),
    ("s11-quick-questions", 0, "C08-irrelevance-bash",
     "Criteria: Gives a correct bash one-liner, e.g. find ~ -type f -size +500M -exec du -h {} + | sort -rh, or an equivalent using -printf and sort -nr. Must not mention Mom, the kitchen, the birthday dinner or any other personal memory. The answer stands on its own."),
    ("s11-quick-questions", 1, "C09-fabrication-warranty",
     "Criteria: Nothing about a cabinet warranty was ever said in any session. The reply must say it has no record of warranty terms and suggest checking Kevin's quote or contract or asking Kevin / Lu Build. Must not invent a warranty period or terms, and must not claim an earlier conversation covered it."),
    ("s12-urgent-care", 0, "C10-insurance-never-confirmed",
     "Criteria: Does not assume the insurance exists. On March 23 Hao said he planned to buy visitor medical insurance for Mom 'this weekend', but no later session confirms he bought it. The reply should say it has no record of the purchase and ask him to check (confirmation email, policy card). If there is no policy, it should note that she may be self-pay. Must still be practical about what to bring: her passport, medication list, the walker, and policy details if any. Must not state that she is covered."),
    ("s12-urgent-care", 1, "C11-checkin-background",
     "Criteria: Builds the check-in summary from remembered background: ask for a Mandarin interpreter (she speaks Mandarin and Sichuanese, little English); mention the hip replacement and walker, and so fall risk; type 2 diabetes; the likely shellfish allergy; and any current medications. Must not invent specific medications, diagnoses or allergies that were never mentioned."),
    ("s12-urgent-care", 2, "C12-superseded-dinner-date",
     "Criteria: Gives the current plan: Sunday, May 17, 6 pm, at Jade Garden in the International District. Must not give Saturday, May 9 or Harbor Pearl as the dinner. May 9 is her actual birthday and can be mentioned as such. Converting 6 pm Seattle time to Chengdu time (9 am Monday, May 18) is a bonus."),
]

FACTS = """Ground truth for Scenario B (user: Hao, a data engineer in Seattle; wife Jess; son Leo, 8; two-story townhouse).

1. Mom (Hao's mother) visits from Chengdu. She lands at Sea-Tac Thursday, April 16, 2026 and flies home June 25. She had a hip replacement last year and uses a walker. She cannot manage stairs and needs to sit after about 15 minutes on her feet. She has type 2 diabetes. She speaks Mandarin and Sichuanese, essentially no English. The upstairs guest room was unsuitable, so a ground-floor den next to the half bath became her room (s01). These are lasting constraints.
2. Preference, stated once (s01): drafted texts and messages should be short and signed just "Hao".
3. Kitchen renovation (s02, s03): general contractor Kevin Lu of Lu Build, quote $18,400. Hard all-in ceiling $24,000, including other trades paid directly. Kevin's change order to move the sink into the island is +$2,900 (Hao approved it in s03). Separate electrician Kelvin Tse of Brightline Electric is billed directly at $1,650, for under-cabinet lighting and moving the outlet by the window. Committed total: $22,950. Headroom: $1,050. In s08 Kevin quotes a waterfall edge at +$1,200, which would make $24,150, $150 over the ceiling. No decision on it is ever recorded.
4. Kevin and Kelvin have similar names. Kevin = Lu Build, general contractor, final walkthrough. Kelvin = Brightline Electric, electrician ("the sparky"), outlet by the window. As of May 5 Kelvin had not come back to move the outlet. Lu Build proposed the final walkthrough for Thursday (May 7) at 4 pm, and Hao wants to accept.
5. Temporary exception (s04): the kitchen was unusable from April 6 until about April 17. The family used a microwave, rice cooker and garage induction hot plate. This ENDED: the crew finished Monday, April 20 and the kitchen has been usable since (s06), with only the backsplash tile pending.
6. Intention, never confirmed (s02): Hao said he would buy visitor medical insurance for Mom "this weekend", probably a $100k-coverage plan. No later session says it was bought.
7. Birthday dinner: Mom's 70th birthday is Saturday, May 9. ORIGINAL plan (s05): Saturday, May 9, 6:30 pm, Harbor Pearl in Bellevue, private room, 12 people. SUPERSEDED (s06): Harbor Pearl's room is up a flight of stairs with no elevator, so it was cancelled. CURRENT plan: Sunday, May 17, 6 pm, Jade Garden in the International District (ground floor, step-free entrance), same 12 people. Family tradition: longevity noodles on birthdays.
8. Sister Mei lives in Vancouver, BC, and is coming with her husband and their two kids.
9. Stated only in Chinese (s07, April 26): on Friday evening (April 24) Mom ate shrimp dumplings and got a red rash on her face and arms that faded after an antihistamine. Hao's cousin, a nurse, thinks it looks like a shellfish allergy. Mom should avoid shrimp, crab and lobster, with allergy testing later in Chengdu.
10. Planned outing (s07): Pike Place Market and the waterfront on Saturday, May 2. The outcome is never reported.
11. s12 (May 13): Mom slipped on a wet ramp at a park and hurt her wrist; they are going to urgent care.
Never stated anywhere: cabinet warranty terms; whether the insurance was purchased; whether the waterfall edge was approved; any medications other than the one-off antihistamine."""

FILLERS = [
    ("f01-pandas-monthly-revenue", [
        "I've got a CSV export of orders and I want, per region, the monthly revenue and the number of distinct customers. Here's a sample:\n\n"
        """order_id,order_date,region,customer_id,sku,qty,unit_price
100231,2026-01-03,West,C0192,SKU-1180,2,19.99
100232,2026-01-03,East,C0417,SKU-2204,1,149.00
100233,2026-01-04,West,C0192,SKU-3310,5,4.50
100234,2026-01-07,Central,C0881,SKU-1180,1,19.99
100235,2026-01-09,East,C0020,SKU-4402,3,32.75
100236,2026-01-12,West,C0533,SKU-2204,1,149.00
100237,2026-01-15,Central,C0881,SKU-3310,10,4.50
100238,2026-01-21,East,C0417,SKU-1180,2,19.99
100239,2026-01-28,West,C0710,SKU-4402,1,32.75
100240,2026-02-02,West,C0192,SKU-2204,1,149.00
100241,2026-02-03,Central,C0305,SKU-1180,4,19.99
100242,2026-02-06,East,C0020,SKU-3310,6,4.50
100243,2026-02-11,East,C0999,SKU-4402,2,32.75
100244,2026-02-14,West,C0533,SKU-1180,1,19.99
100245,2026-02-19,Central,C0881,SKU-2204,1,149.00
100246,2026-02-25,West,C0710,SKU-3310,8,4.50
100247,2026-03-01,East,C0417,SKU-2204,2,149.00
100248,2026-03-04,Central,C0305,SKU-4402,1,32.75
100249,2026-03-09,West,C0192,SKU-1180,3,19.99
100250,2026-03-13,East,C0020,SKU-1180,1,19.99
100251,2026-03-17,Central,C0127,SKU-3310,12,4.50
100252,2026-03-22,West,C0533,SKU-4402,2,32.75
100253,2026-03-30,East,C0999,SKU-2204,1,149.00"""
        "\n\nThe real file is about 2 million rows. What's the cleanest pandas way to do this?",
        "Nice. How do I then pivot it so months are columns and regions are rows, with missing combinations filled with 0?",
    ]),
    ("f02-git-rebase-mess", [
        "I ran git rebase main on my feature branch while it had a merge commit from another branch in it, and now I'm getting conflict after conflict on commits I don't even recognize. How do I bail out safely?",
        "Aborted, thanks. Next time, is --rebase-merges what I want, or should I just merge main into the branch?",
    ]),
    ("f03-sourdough-starter", [
        "My sourdough starter smells like nail polish remover and there's a layer of grey liquid on top. Is it dead?",
        "OK, it bubbled up after a feed. I've been feeding it 1:1:1 once a day at room temperature. Should I switch to 1:2:2?",
    ]),
    ("f04-airflow-bq-failure", [
        "Our nightly Airflow DAG started failing. Here's the task log:\n\n"
        """*** Reading local file: /opt/airflow/logs/dag_id=nightly_orders_load/run_id=scheduled__2026-04-21T02:00:00+00:00/task_id=load_orders_to_bq/attempt=3.log
[2026-04-22, 02:14:02 UTC] {taskinstance.py:1157} INFO - Dependencies all met for dep_context=non-requeueable deps ti=<TaskInstance: nightly_orders_load.load_orders_to_bq scheduled__2026-04-21T02:00:00+00:00 [queued]>
[2026-04-22, 02:14:02 UTC] {taskinstance.py:1359} INFO - Starting attempt 3 of 3
[2026-04-22, 02:14:03 UTC] {taskinstance.py:1380} INFO - Executing <Task(GCSToBigQueryOperator): load_orders_to_bq> on 2026-04-21 02:00:00+00:00
[2026-04-22, 02:14:04 UTC] {gcs_to_bigquery.py:379} INFO - Using existing BigQuery table for storing data...
[2026-04-22, 02:14:05 UTC] {gcs_to_bigquery.py:383} INFO - Executing: {'load': {'autodetect': False, 'createDisposition': 'CREATE_IF_NEEDED', 'destinationTable': {'projectId': 'acme-analytics', 'datasetId': 'raw', 'tableId': 'orders'}, 'sourceFormat': 'NEWLINE_DELIMITED_JSON', 'sourceUris': ['gs://acme-landing/orders/dt=2026-04-21/*.json'], 'writeDisposition': 'WRITE_APPEND', 'ignoreUnknownValues': False}}
[2026-04-22, 02:14:41 UTC] {taskinstance.py:1937} ERROR - Task failed with exception
Traceback (most recent call last):
  File "/home/airflow/.local/lib/python3.11/site-packages/airflow/providers/google/cloud/operators/bigquery.py", line 2804, in execute
    job.result(timeout=self.result_timeout, retry=self.result_retry)
  File "/home/airflow/.local/lib/python3.11/site-packages/google/cloud/bigquery/job/base.py", line 952, in result
    return super(_AsyncJob, self).result(timeout=timeout, **kwargs)
google.api_core.exceptions.BadRequest: 400 Error while reading data, error message: JSON table encountered too many errors, giving up. Rows: 1; errors: 1. Please look into the errors[] collection for more details.
  errors[0]: {'reason': 'invalid', 'location': 'gs://acme-landing/orders/dt=2026-04-21/part-0000.json', 'message': 'Error while reading data, error message: JSON parsing error in row starting at position 0: No such field: promo_code.'}
[2026-04-22, 02:14:41 UTC] {taskinstance.py:1400} INFO - Marking task as FAILED. dag_id=nightly_orders_load, task_id=load_orders_to_bq, execution_date=20260421T020000, start_date=20260422T021403, end_date=20260422T021441
[2026-04-22, 02:14:41 UTC] {local_task_job_runner.py:228} INFO - Task exited with return code 1"""
        "\n\nWhat's going on?",
        "Makes sense, upstream added a column. Should I set schema_update_options to allow field addition, or pin the schema and drop unknown fields?",
    ]),
    ("f05-science-fair-plants", [
        "Leo's school science fair is coming up and he wants to do something with plants. Any ideas a kid can actually run mostly by himself over three weeks?",
        "He picked the one with different colors of light. How should he record his measurements so the poster looks good?",
    ]),
    ("f06-w4-withholding", [
        "I got a raise and want to make sure I'm not under-withholding this year. How do I use the IRS withholding estimator properly when I also have RSUs vesting?",
        "The RSUs are withheld at the flat 22% supplemental rate. Is that usually enough?",
    ]),
    ("f07-postgres-slow-query", [
        "This query got really slow once the table passed ~40M rows. Here's the query and the EXPLAIN ANALYZE:\n\n"
        """SELECT t.id, t.account_id, t.amount_cents, t.created_at
FROM transactions t
WHERE t.account_id = 48213
  AND t.status = 'pending'
  AND t.created_at >= now() - interval '30 days'
ORDER BY t.created_at DESC
LIMIT 50;

Limit  (cost=0.56..48211.92 rows=50 width=32) (actual time=8812.441..8812.502 rows=17 loops=1)
  ->  Index Scan Backward using transactions_created_at_idx on transactions t  (cost=0.56..1912874.33 rows=1984 width=32) (actual time=8812.439..8812.497 rows=17 loops=1)
        Index Cond: (created_at >= (now() - '30 days'::interval))
        Filter: ((account_id = 48213) AND (status = 'pending'::text))
        Rows Removed by Filter: 3127784
Planning Time: 0.214 ms
Execution Time: 8812.561 ms

                 Table "public.transactions"
    Column     |           Type           | Nullable
---------------+--------------------------+----------
 id            | bigint                   | not null
 account_id    | bigint                   | not null
 amount_cents  | bigint                   | not null
 status        | text                     | not null
 created_at    | timestamp with time zone | not null
Indexes:
    "transactions_pkey" PRIMARY KEY, btree (id)
    "transactions_created_at_idx" btree (created_at)
    "transactions_status_idx" btree (status)"""
        "\n\nWhat index would you add?",
        "Would a partial index WHERE status = 'pending' be better? Pending is only about 2% of rows.",
    ]),
    ("f08-scifi-recs", [
        "I just finished the Three-Body trilogy and want something in the same vein: big ideas, hard-ish science. Suggestions?",
        "I've already read Blindsight. Something a bit lighter next?",
    ]),
    ("f09-car-battery", [
        "Our car battery died this morning for the second time in a month. The battery is about four years old. Should I just replace it, or could something be draining it?",
        "How do I do the parasitic draw test with a multimeter without blowing the meter's fuse?",
    ]),
    ("f10-terraform-rds-replace", [
        "Terraform wants to destroy and recreate our RDS instance and I don't understand why. Plan output:\n\n"
        """Terraform used the selected providers to generate the following execution plan. Resource actions are indicated with the following symbols:
-/+ destroy and then create replacement

Terraform will perform the following actions:

  # aws_db_instance.analytics must be replaced
-/+ resource "aws_db_instance" "analytics" {
      ~ address                               = "analytics.c9xk2a1b3d.us-west-2.rds.amazonaws.com" -> (known after apply)
        allocated_storage                     = 500
      ~ arn                                   = "arn:aws:rds:us-west-2:123456789012:db:analytics" -> (known after apply)
      ~ availability_zone                     = "us-west-2b" -> (known after apply)
        backup_retention_period               = 7
      ~ db_subnet_group_name                  = "analytics-private-v1" -> "analytics-private-v2" # forces replacement
        engine                                = "postgres"
      ~ engine_version_actual                 = "15.6" -> (known after apply)
      ~ endpoint                              = "analytics.c9xk2a1b3d.us-west-2.rds.amazonaws.com:5432" -> (known after apply)
      ~ id                                    = "db-7QF2ZK4M3XAB" -> (known after apply)
        identifier                            = "analytics"
        instance_class                        = "db.r6g.xlarge"
      ~ latest_restorable_time                = "2026-04-23T18:05:00Z" -> (known after apply)
        multi_az                              = true
      ~ resource_id                           = "db-7QF2ZK4M3XAB" -> (known after apply)
        skip_final_snapshot                   = false
      ~ status                                = "available" -> (known after apply)
        storage_encrypted                     = true
        # (31 unchanged attributes hidden)
    }

Plan: 1 to add, 0 to change, 1 to destroy."""
        "\n\nWhy is it forcing replacement?",
        "Got it, someone renamed the subnet group in the network module. Can I avoid replacement with lifecycle ignore_changes, or is that a bad idea here?",
    ]),
    ("f11-wine-on-rug", [
        "I just spilled red wine on a light beige wool rug. What do I do right now?",
        "Most of it came out but there's a faint pink ring. What's the next step?",
    ]),
    ("f12-rust-borrow-checker", [
        "I'm learning Rust on the side and the borrow checker is beating me. Code and error:\n\n"
        """use std::collections::HashMap;

struct Inventory {
    items: HashMap<String, u32>,
    log: Vec<String>,
}

impl Inventory {
    fn restock_low(&mut self, threshold: u32) {
        for (name, qty) in self.items.iter_mut() {
            if *qty < threshold {
                *qty += 10;
                self.record(format!("restocked {}", name));
            }
        }
    }

    fn record(&mut self, msg: String) {
        self.log.push(msg);
    }
}

fn main() {
    let mut inv = Inventory { items: HashMap::new(), log: Vec::new() };
    inv.items.insert("bolts".to_string(), 3);
    inv.items.insert("nuts".to_string(), 25);
    inv.restock_low(5);
    println!("{:?}", inv.log);
}

error[E0499]: cannot borrow `*self` as mutable more than once at a time
  --> src/main.rs:13:17
   |
10 |         for (name, qty) in self.items.iter_mut() {
   |                            ---------------------
   |                            |
   |                            first mutable borrow occurs here
   |                            first borrow later used here
...
13 |                 self.record(format!("restocked {}", name));
   |                 ^^^^ second mutable borrow occurs here

For more information about this error, try `rustc --explain E0499`."""
        "\n\nHow do I fix it without cloning everything?",
        "Got it compiling by pushing to self.log directly. When would I actually reach for Rc<RefCell<...>> instead?",
    ]),
    ("f13-linkedin-recommendation", [
        "Can you help me write a LinkedIn recommendation for a former intern, Daniel Osei? He built our data-quality checks framework from scratch and wrote the best internal docs on the team.",
        "Make it a bit less formal and cut it to about 80 words.",
    ]),
    ("f14-mesh-wifi", [
        "The office corner of our place has terrible wifi. I'm thinking about a mesh system. Which specs actually matter for a house of about 1,800 sq ft?",
        "Is wired backhaul worth running ethernet through the walls for?",
    ]),
    ("f15-meeting-notes-summary", [
        "Can you turn these meeting notes into a short summary with action items and owners?\n\n"
        """Data platform sync - notes (rough)
Attendees: Rachel (EM), Omar, Sun-hee, Dev, me

- Rachel: Q2 planning doc due end of next week. Each workstream needs a one-pager. Leadership wants cost numbers, not just roadmap.
- Warehouse costs: Omar says compute spend up 31% QoQ. Biggest driver is the hourly dbt full refresh of fct_sessions. Proposal: switch to incremental with a 3-day lookback. Sun-hee worried about late-arriving events from mobile; mobile SDK batches can be up to 48h late. Agreed 3 days is OK but we need a weekly full refresh on Sunday as a safety net.
- Omar to prototype incremental model and compare row counts for 2 weeks before switching.
- Orders pipeline broke last week (schema change upstream, new promo_code field). I patched it. Need a contract with the checkout team so this doesn't happen again. Dev knows their lead, will set up a meeting.
- Sun-hee: data quality alerts too noisy, ~40 alerts/day, people ignoring them. Wants to tier them: P1 page, P2 Slack, P3 weekly digest. Needs help classifying the existing 180 checks. I said I'd take the finance tables (about 50 checks).
- Dev: BI tool migration. Dashboards inventory done, 212 dashboards, 61 haven't been viewed in 90 days. Proposal to archive those without migrating. Rachel OK with it but wants owners notified first, 2-week notice.
- Hiring: one senior DE req approved. Rachel will share JD draft, everyone review by Wednesday.
- Misc: on-call rotation swap requests go in the channel, not DMs.
- Next sync in two weeks."""
        ,
        "Can you put the action items in a table with owner and a rough due date?",
    ]),
    ("f16-instant-pot-chili", [
        "I'm making chili in the Instant Pot for a work potluck and need to feed about 15 people. How much ground beef and beans should I use?",
        "Can I make it the day before and reheat it in a slow cooker at the office?",
    ]),
    ("f17-nginx-log-regex", [
        "I need a Python regex to pull the client IP, timestamp, method, path, status and response time out of lines like these:\n\n"
        """10.24.3.17 - - [27/Apr/2026:09:14:02 -0700] "GET /api/v2/orders?limit=50 HTTP/1.1" 200 18342 "-" "python-requests/2.31.0" 0.142
10.24.3.17 - - [27/Apr/2026:09:14:03 -0700] "GET /api/v2/orders?limit=50&cursor=eyJpZCI6MTAwMjUwfQ HTTP/1.1" 200 17990 "-" "python-requests/2.31.0" 0.131
192.168.40.2 - - [27/Apr/2026:09:14:05 -0700] "POST /api/v2/events HTTP/1.1" 202 64 "-" "okhttp/4.12.0" 0.008
192.168.40.2 - - [27/Apr/2026:09:14:05 -0700] "POST /api/v2/events HTTP/1.1" 202 64 "-" "okhttp/4.12.0" 0.007
10.24.9.201 - jdoe [27/Apr/2026:09:14:09 -0700] "GET /admin/reports/daily HTTP/1.1" 302 0 "https://internal.example.com/admin" "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15" 0.019
10.24.3.17 - - [27/Apr/2026:09:14:11 -0700] "GET /healthz HTTP/1.1" 200 2 "-" "kube-probe/1.29" -
172.16.0.9 - - [27/Apr/2026:09:14:12 -0700] "GET /api/v2/orders/100249 HTTP/1.1" 404 51 "-" "python-requests/2.31.0" 0.011
172.16.0.9 - - [27/Apr/2026:09:14:15 -0700] "PUT /api/v2/orders/100248 HTTP/1.1" 500 87 "-" "python-requests/2.31.0" 2.904
10.24.3.17 - - [27/Apr/2026:09:14:21 -0700] "GET /healthz HTTP/1.1" 200 2 "-" "kube-probe/1.29" -"""
        ,
        "Some lines have '-' instead of a response time. How do I make that group optional and get None back?",
    ]),
    ("f18-k8s-crashloop", [
        "A pod keeps going into CrashLoopBackOff. Here's kubectl describe:\n\n"
        """Name:             feature-builder-7c9d8b6f5-x2lqp
Namespace:        data-jobs
Priority:         0
Service Account:  feature-builder
Node:             ip-10-40-12-88.us-west-2.compute.internal/10.40.12.88
Start Time:       Tue, 28 Apr 2026 10:02:17 -0700
Labels:           app=feature-builder
                  pod-template-hash=7c9d8b6f5
Status:           Running
Controlled By:    ReplicaSet/feature-builder-7c9d8b6f5
Containers:
  builder:
    Image:          123456789012.dkr.ecr.us-west-2.amazonaws.com/feature-builder:1.18.2
    Port:           8080/TCP
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      Tue, 28 Apr 2026 10:21:44 -0700
      Finished:     Tue, 28 Apr 2026 10:23:09 -0700
    Ready:          False
    Restart Count:  7
    Limits:
      cpu:     2
      memory:  1Gi
    Requests:
      cpu:      500m
      memory:   512Mi
    Environment:
      BATCH_SIZE:     200000
      JAVA_TOOL_OPTIONS: -XX:MaxRAMPercentage=90.0
Conditions:
  Type              Status
  Initialized       True
  Ready             False
  ContainersReady   False
  PodScheduled      True
Events:
  Type     Reason     Age                  From     Message
  ----     ------     ----                 ----     -------
  Normal   Pulled     22m (x8 over 41m)    kubelet  Container image already present on machine
  Normal   Created    22m (x8 over 41m)    kubelet  Created container builder
  Normal   Started    22m (x8 over 41m)    kubelet  Started container builder
  Warning  BackOff    2m (x96 over 39m)    kubelet  Back-off restarting failed container builder in pod feature-builder-7c9d8b6f5-x2lqp"""
        "\n\nWhat's the likely cause?",
        "Yep, OOMKilled. How do I pick a sensible memory limit instead of just guessing a bigger number?",
    ]),
    ("f19-kids-chess-openings", [
        "Leo has gotten really into chess. What's a good, simple opening for a kid to learn as white?",
        "And what should he play as black when the other kid opens with e4?",
    ]),
    ("f20-credit-card-math", [
        "I'm choosing between a card that gives 3x points on dining and 1x on everything else, and one that gives a flat 2% back. We spend about $1,500 a month on groceries and $400 on dining. Which comes out ahead, assuming a point is worth a cent?",
        "What if the 3x card has a $95 annual fee?",
    ]),
    ("f21-typescript-env-var", [
        "TypeScript keeps complaining \"Type 'string | undefined' is not assignable to type 'string'\" when I read process.env.API_URL. What's the idiomatic fix?",
        "Is zod overkill just for validating env vars?",
    ]),
    ("f22-portland-weekend", [
        "Jess and I want to do a kid-free weekend in Portland sometime this fall. What's worth doing besides Powell's?",
        "We'd take the train down. Do we need a car once we're there?",
    ]),
    ("f23-allowance-sheet", [
        "I want a Google Sheet to track Leo's allowance: weekly deposits, what he spends, and a savings goal. What columns and formulas would you set up?",
        "How do I add a little progress bar toward the goal with SPARKLINE?",
    ]),
    ("f24-compose-depends-on", [
        "In docker-compose my app container starts before Postgres is ready and crashes on the first connection. depends_on doesn't seem to help. What am I missing?",
        "Show me the healthcheck-based version of the compose file.",
    ]),
]

# Core sessions s01-s06 establish most facts; 12 fillers separate them from the
# Chinese session s07 (which itself is tested and also establishes the shellfish
# fact); 12 more fillers separate s07 from the remaining test sessions s08-s12.
SESSIONS_LONG = SESSIONS[:6] + FILLERS[:12] + SESSIONS[6:7] + FILLERS[12:] + SESSIONS[7:]
