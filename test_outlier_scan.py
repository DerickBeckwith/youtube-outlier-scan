#!/usr/bin/env python3
"""Offline tests for outlier_scan.py. Fakes the YouTube API at the urlopen layer,
so URL construction, batching, parsing, filtering, ranking, and quota
accounting are all exercised without a key."""

import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request

os.environ["YOUTUBE_API_KEY"] = "FAKE"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outlier_scan  # noqa: E402

REQUESTS = []   # every request the script makes, for assertions
FAILURES = []


def check(name, cond, detail=""):
    (print(f"  PASS  {name}") if cond
     else (print(f"  FAIL  {name}  {detail}"), FAILURES.append(name)))


# ---------------------------------------------------------------- fake world
# ch_a: small channel, one genuine outlier + ordinary uploads
# ch_b: big channel, should be filtered out by the subscriber ceiling
# ch_c: small channel but the video is a Short, filtered by runtime
# ch_d: hides its subscriber count, dropped by default
CHANNELS = {
    "ch_a": {"subs": "8000", "hidden": False},
    "ch_b": {"subs": "2400000", "hidden": False},
    "ch_c": {"subs": "3000", "hidden": False},
    "ch_d": {"subs": None, "hidden": True},
}
VIDEOS = {
    "vid_a1": ("ch_a", "SOC 2 cost breakdown", 180000, "PT14M02S"),
    "vid_b1": ("ch_b", "Big channel SOC 2 explainer", 900000, "PT11M"),
    "vid_c1": ("ch_c", "SOC 2 in 60 seconds", 400000, "PT58S"),
    "vid_d1": ("ch_d", "Hidden subs channel", 250000, "PT9M"),
}
# ch_a's back catalogue: median should land at 12,000
BACKLOG = {"ch_a": [9000, 11000, 13000, 15000, 40000]}
# the uploads playlist also returns the candidate itself, as YouTube does


def fake_urlopen(req, timeout=None):
    url = req.full_url
    parts = urllib.parse.urlparse(url)
    endpoint = parts.path.rstrip("/").split("/")[-1]
    q = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
    REQUESTS.append((endpoint, q))

    if endpoint == "search":
        body = {"items": [{"id": {"kind": "youtube#video", "videoId": v}}
                          for v in VIDEOS]}
    elif endpoint == "videos":
        items = []
        for vid in q["id"].split(","):
            if vid in VIDEOS:
                ch, title, views, dur = VIDEOS[vid]
            elif vid.startswith("back_"):
                ch, title = "ch_a", "old upload"
                views, dur = BACKLOG["ch_a"][int(vid.split("_")[-1])], "PT10M"
            else:
                continue
            items.append({
                "id": vid,
                "snippet": {"title": title, "channelId": ch,
                            "channelTitle": ch.upper(),
                            "publishedAt": "2026-02-01T00:00:00Z",
                            "thumbnails": {"medium": {"url": f"http://t/{vid}.jpg"}}},
                "statistics": {"viewCount": str(views), "likeCount": "10"},
                "contentDetails": {"duration": dur},
            })
        body = {"items": items}
    elif endpoint == "channels":
        items = []
        for cid in q["id"].split(","):
            c = CHANNELS[cid]
            st = {"videoCount": "40"}
            if c["hidden"]:
                st["hiddenSubscriberCount"] = True
            else:
                st["subscriberCount"] = c["subs"]
            items.append({"id": cid, "statistics": st,
                          "contentDetails": {"relatedPlaylists":
                                             {"uploads": f"UU{cid}"}}})
        body = {"items": items}
    elif endpoint == "playlistItems":
        n = len(BACKLOG["ch_a"])
        body = {"items": [{"contentDetails": {"videoId": "vid_a1"}}]
                + [{"contentDetails": {"videoId": f"back_{i}"}}
                   for i in range(n)]}
    else:
        raise AssertionError(f"unexpected endpoint {endpoint}")

    resp = io.BytesIO(json.dumps(body).encode())
    resp.__enter__ = lambda: resp
    resp.__exit__ = lambda *a: False
    return resp


urllib.request.urlopen = fake_urlopen

# ------------------------------------------------------------------- run it
sys.argv = ["outlier_scan.py", "soc 2 compliance",
            "--min-views", "50000", "--max-subs", "50000",
            "--min-ratio", "3", "--out", "/tmp/t"]
outlier_scan.main()

print("\nassertions:")

# regression test for the bug that would have 400'd on the first real call
bad = [(e, q) for e, q in REQUESTS
       if e in ("videos", "channels") and "id" in q and "maxResults" in q]
check("no maxResults sent alongside id", not bad, str(bad[:1]))

rows = list(csv.DictReader(open("/tmp/t.csv")))
ids = [r["video_id"] for r in rows]

check("outlier kept", "vid_a1" in ids)
check("over-ceiling channel dropped", "vid_b1" not in ids)
check("Short dropped by runtime floor", "vid_c1" not in ids)
check("hidden-sub channel dropped by default", "vid_d1" not in ids)
check("exactly one survivor", len(rows) == 1, f"got {len(rows)}")

r = rows[0]
check("ratio computed", abs(float(r["ratio"]) - 180000 / 8000) < 0.01,
      f"got {r['ratio']} want 22.5")
check("outlier multiple vs channel median",
      abs(float(r["outlier_multiple"]) - 180000 / 13000) < 0.01,
      f"got {r['outlier_multiple']} want 13.85")
check("candidate excluded from its own median",
      abs(float(r["outlier_multiple"]) - 180000 / 15000) > 0.01,
      "median still includes the candidate")
check("duration parsed into seconds", r["seconds"] == "842")

endpoints = [e for e, _ in REQUESTS]
check("one search call", endpoints.count("search") == 1)
check("videos batched, not per-id", endpoints.count("videos") == 2,
      f"got {endpoints.count('videos')}")
check("quota accounting", outlier_scan.Quota.used == 100 + 1 + 1 + 1 + 1,
      f"got {outlier_scan.Quota.used}")

doc = open("/tmp/t.html").read()
check("HTML renders the survivor", "SOC 2 cost breakdown" in doc)
check("HTML has no unrendered f-string braces", "{r[" not in doc)
check("HTML links the video", "youtu.be/vid_a1" in doc)

# hidden-subs opt-in path
REQUESTS.clear()
outlier_scan.Quota.used = 0
sys.argv += ["--allow-hidden-subs", "--no-median"]
outlier_scan.main()
ids2 = [r["video_id"] for r in csv.DictReader(open("/tmp/t.csv"))]
check("--allow-hidden-subs admits the hidden channel", "vid_d1" in ids2)

print(f"\n{len(FAILURES)} failures" if FAILURES else "\nall assertions passed")
sys.exit(1 if FAILURES else 0)
