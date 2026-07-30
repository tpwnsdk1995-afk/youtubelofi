"""셔플백(shuffle-bag) 방식으로 곡/씬/문구 로테이션 상태를 관리한다.

각 풀(pool)은 독립적으로 순환한다: 전체 항목이 한 번씩 다 뽑히기 전에는
같은 항목이 반복되지 않고, 다 뽑히면 재셔플해서 다시 순환한다.
"""

import json
import random
from pathlib import Path


def load_state(path):
    p = Path(path)
    if not p.exists():
        return {"pools": {}, "recent_videos": []}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_state(path, state):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _ensure_pool(state, pool_name, full_pool):
    pools = state.setdefault("pools", {})
    pool = pools.setdefault(pool_name, {"remaining": [], "history": []})
    full_set = set(full_pool)
    # 라이브러리/설정에서 사라진 항목은 제거 (예: 음원 파일 삭제됨)
    pool["remaining"] = [x for x in pool["remaining"] if x in full_set]
    pool["history"] = [x for x in pool["history"] if x in full_set]
    known = set(pool["remaining"]) | set(pool["history"])
    new_items = [x for x in full_pool if x not in known]
    if new_items:
        pool["remaining"].extend(new_items)
    return pool


def draw(state, pool_name, full_pool, count=1, rng=None):
    """pool_name 풀에서 count개를 셔플백 방식으로 뽑아 state를 갱신하고 반환한다.

    full_pool: 현재 이 풀에 존재하는 전체 항목 리스트 (예: music_library 파일 목록,
    scenes.yml의 씬 id 목록 등). 매 호출마다 최신 목록을 넘겨야 신규/삭제 항목이 반영된다.
    """
    if not full_pool:
        raise ValueError(f"pool '{pool_name}' is empty")
    rng = rng or random
    pool = _ensure_pool(state, pool_name, full_pool)
    drawn = []
    used_this_call = set()
    for _ in range(count):
        if not pool["remaining"]:
            # 이번 호출에서 이미 뽑은 항목은 가능하면 재셔플 후보에서 제외해
            # 같은 호출 안에서 즉시 중복되지 않도록 한다 (풀 크기가 count 이상일 때 보장됨).
            candidates = [x for x in full_pool if x not in used_this_call]
            if not candidates:
                candidates = list(full_pool)
            pool["remaining"] = candidates
            rng.shuffle(pool["remaining"])
            pool["history"] = []
        item = pool["remaining"].pop()
        pool["history"].append(item)
        drawn.append(item)
        used_this_call.add(item)
    return drawn


def record_video(state, entry, keep_last=50):
    videos = state.setdefault("recent_videos", [])
    videos.append(entry)
    if len(videos) > keep_last:
        del videos[: len(videos) - keep_last]
