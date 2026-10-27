"""Frozen synthetic histories and a separate oracle; no provider receives the oracle."""

from __future__ import annotations

import random

from membrain.helpers import digest

DATASET_VERSION = "long-memory-synthetic-v1"
DEFAULT_SEED = 20261025
MIN_HISTORY_TURNS = 100
MAX_HISTORY_TURNS = 128


def long_history(seed=DEFAULT_SEED, history_turns=104):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be a bounded integer")
    if type(history_turns) is not int or not MIN_HISTORY_TURNS <= history_turns <= MAX_HISTORY_TURNS:
        raise ValueError("Long-history experiments require 100 to 128 real turns")
    rng = random.Random(seed)
    names = rng.sample(["苔桥", "松舟", "雨塔", "纸湾", "竹铃", "星岸", "石帆", "云井"], 5)
    plan_name, school_name, companion, key_place, unrelated = names
    history = []
    for index in range(history_turns):
        number = rng.randrange(1000, 9999)
        topic = ("植物观察", "纸模型", "厨房清单", "阅读索引", "城市速写", "音乐笔记")[index % 6]
        text = f"这是我的第{index + 1}条日常笔记：{topic}的标签是{unrelated}{number}，放在第{index % 9 + 1}页。"
        history.append({"name": f"history-{index:03d}", "text": text, "brain": "trained", "learn": True,
                        "review": "Acknowledge the current note without inventing external outcomes."})
    anchors = {
        0: f"从2026年10月20日00:00 UTC起，我的长期复习安排叫‘{plan_name}’：先散步，再看教材，没有预定结束日期。",
        3: f"我小时候就读的学校叫‘{school_name}小学’，校门旁有一棵银杏树。我没有告诉你学校所在的城市。",
        17: f"2026年10月21日，我和{companion}原本约在图书馆复习，后来取消了，那次没有见面。",
        31: "只有我正在备考时，我才希望先被倾听，不主动得到建议。其他时候仍可以直接建议。我没说明现在是否备考。",
        48: f"我的备用钥匙放在标着‘{key_place}’的小盒子里。盒子的位置没有告诉你。",
        63: f"2026年10月22日，我和{companion}在博物馆见面复习了。这是另一次约定，不是21日那次取消的约定。",
        history_turns - 15: f"从2026年10月23日00:00 UTC起，‘{plan_name}’长期改成先看教材，再散步，替代之前的顺序，没有预定结束日期。",
    }
    for index, text in anchors.items():
        history[index]["text"] = text
    cases = [
        ("order_change", "回忆我的复习安排：名字是什么，之前和后来的顺序分别是什么？我们约过喝哪种饮料？",
         [0, history_turns - 15], [plan_name, "previous walk then textbook", "later textbook then walk"],
         ["drink agreement"]),
        ("cue_childhood", "我提过校门旁有银杏树的那所小学，它叫什么名字？在哪座城市？",
         [3], [school_name + "小学"], ["school city"]),
        ("similar_episodes", f"我和{companion}那两次复习约定，哪次取消了，哪次见面了，各在哪里？",
         [17, 63], ["2026-10-21 library cancelled, no meeting", "2026-10-22 museum meeting"], []),
        ("condition_unknown", "现在应该直接给我建议，还是先听我说？根据我提过的备考偏好判断，但我没有补充当前状态。",
         [31], ["exam preparation condition", "current condition remains unknown"], ["currently preparing for exam"]),
        ("rare_detail", "我之前说备用钥匙放在哪个标签的盒子里？那个盒子具体放在家里哪里？",
         [48], [key_place], ["box location"]),
        ("false_presupposition", "我们之前是不是约好在海边复习并喝柠檬汽水？回忆一下具体地点和饮料。",
         [], [], ["beach agreement", "lemon soda agreement"]),
    ]
    probes = [{"name": name, "text": text, "brain": "probe-" + name, "learn": False,
               "review": "Independent source-grounded answer review required; no keyword grading."}
              for name, text, *_ in cases]
    oracle = {name: {"source_turns": [f"history-{index:03d}" for index in indices],
                     "supported": supported, "unestablished": absent,
                     "scope": "Only synthetic source reports, not external world verification"}
              for name, _, indices, supported, absent in cases}
    result = {"version": DATASET_VERSION, "seed": seed, "history_turns": history_turns,
              "history": history, "probes": probes, "oracle": oracle,
              "split": "Development smoke is separate; long-history questions are never sent during encoding.",
              "limitations": ["Synthetic seeded cases are reproducible, not an independently human-held-out benchmark.",
                              "Changing a seed is a new experiment, not a retry of a failed result.",
                              "Compressed calendar labels do not establish weeks or years of continuous operation."]}
    return {**result, "sha256": digest(result)}


def acceptance_history(seed=DEFAULT_SEED):
    source = long_history(seed, 100)
    paraphrases = [
        "请梳理我那项长期复习计划的名称和调整前后的先后步骤；如果曾约定饮品，也告诉我是什么。",
        "小时候校门边种着银杏的那所学校，我说过它的名字和所在城市吗？",
        "把我提到的两次复习见面安排分开说：日期、地点，以及最后有没有见成。",
        "我曾怎样限定‘先倾听、别主动建议’这个偏好？仅凭目前的信息，你能确定现在应不应该应用它吗？",
        "备用钥匙收在哪个带标签的小盒里？我交代过小盒在家中的具体位置吗？",
        "你还记得我们定下的海滨复习地点和柠檬汽水吗？请把那项约定说清楚。",
    ]
    probes, oracle = list(source["probes"]), dict(source["oracle"])
    for original, text in zip(source["probes"], paraphrases, strict=True):
        name = original["name"] + "_paraphrase"
        probes.append({**original, "name": name, "text": text, "brain": "probe-" + name})
        oracle[name] = {**source["oracle"][original["name"]], "paraphrase_of": original["name"]}
    result = {**source, "version": "v1-acceptance-history-v1", "probes": probes, "oracle": oracle,
              "source_dataset_sha256": source["sha256"],
              "split": "One 100-exchange campaign; all twelve questions and their oracle remain outside history."}
    result.pop("sha256")
    return {**result, "sha256": digest(result)}


def validate_dataset(dataset):
    if not isinstance(dataset, dict):
        raise ValueError("The frozen history or oracle changed")
    expected = (acceptance_history(dataset.get("seed"))
                if dataset.get("version") == "v1-acceptance-history-v1"
                else long_history(dataset.get("seed"), dataset.get("history_turns")))
    if dataset != expected:
        raise ValueError("The frozen history or oracle changed")
