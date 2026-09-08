#!/usr/bin/env python3
"""
outlier_scan.py - Find outlier YouTube videos via the YouTube Data API v3.

Screens search results for videos that pulled far more views than the
channel publishing them would predict. Three criteria are mechanical:

  1. video views above a floor
  2. channel subscribers below a ceiling
  3. views-to-subscribers ratio above a threshold

A fourth criterion, packaging quality, is a human judgment. The script
emits an HTML contact sheet of thumbnails for eyeball review rather than
guessing at it.

Also computes a channel-relative outlier multiple: this video's views
divided by the median views of the channel's other recent uploads. That
is a stronger demand signal than views-over-subscribers, because it
controls for a channel that is simply large or simply new.

The raw counts come from the YouTube Data API. The ratio and the outlier
multiple are computed locally by this script and are not YouTube data.

stdlib only. Requires YOUTUBE_API_KEY in the environment.
See README.md for how to get one.

Quota (default 10,000 units/day per Google Cloud project, resets at
midnight Pacific):
  search.list        100 units per page
  videos.list          1 unit  per batch of 50
  channels.list        1 unit  per batch of 50
  playlistItems.list   1 unit  per batch of 50
The searches dominate. ~95 keyword-pages per day is the practical ceiling.
"""

import argparse
import csv
import html
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://www.googleapis.com/youtube/v3/"
DURATION_RE = re.compile(
    r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
)


class Quota:
    used = 0

    @classmethod
    def spend(cls, units):
        cls.used += units


def api_get(endpoint, params, key, cost):
    params = dict(params)
    params["key"] = key
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            Quota.spend(cost)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        Quota.spend(cost)
        body = e.read().decode("utf-8", "replace")
        if e.code == 403 and "quotaExceeded" in body:
            sys.exit(
                "Quota exhausted for today. It resets at midnight Pacific.\n"
                f"Units spent this run: {Quota.used}"
            )
        sys.exit(f"API error {e.code} on {endpoint}: {body[:400]}")


def parse_duration(iso):
    """ISO 8601 duration -> seconds. Returns 0 if unparseable."""
    m = DURATION_RE.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def chunk(seq, n=50):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def search_videos(keyword, key, pages, order, published_after, region):
    """Returns a list of video IDs. Costs 100 units per page."""
    ids, token = [], None
    for _ in range(pages):
        params = {
            "part": "id",
            "type": "video",
            "q": keyword,
            "maxResults": 50,
            "order": order,
            "regionCode": region,
            "relevanceLanguage": "en",
        }
        if published_after:
            params["publishedAfter"] = published_after
        if token:
            params["pageToken"] = token
        data = api_get("search", params, key, cost=100)
        ids += [i["id"]["videoId"] for i in data.get("items", [])
                if i.get("id", {}).get("videoId")]
        token = data.get("nextPageToken")
        if not token:
            break
    return ids


def fetch_videos(video_ids, key):
    out = {}
    for batch in chunk(video_ids):
        # maxResults is NOT supported alongside the id parameter. Passing both
        # returns a 400. The batch size is capped by chunk() instead.
        data = api_get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
        }, key, cost=1)
        for v in data.get("items", []):
            st, sn = v.get("statistics", {}), v.get("snippet", {})
            out[v["id"]] = {
                "video_id": v["id"],
                "title": sn.get("title", ""),
                "channel_id": sn.get("channelId", ""),
                "channel_title": sn.get("channelTitle", ""),
                "published_at": sn.get("publishedAt", "")[:10],
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)) if "likeCount" in st else None,
                "comments": int(st.get("commentCount", 0)) if "commentCount" in st else None,
                "seconds": parse_duration(
                    v.get("contentDetails", {}).get("duration", "")),
                "thumb": (sn.get("thumbnails", {}).get("medium", {}) or {}).get("url", ""),
            }
    return out


def fetch_channels(channel_ids, key):
    out = {}
    for batch in chunk(sorted(set(channel_ids))):
        # Same restriction as videos.list: no maxResults with id.
        data = api_get("channels", {
            "part": "statistics,contentDetails",
            "id": ",".join(batch),
        }, key, cost=1)
        for c in data.get("items", []):
            st = c.get("statistics", {})
            hidden = st.get("hiddenSubscriberCount", False)
            out[c["id"]] = {
                "subs": None if hidden else int(st.get("subscriberCount", 0)),
                "uploads": (c.get("contentDetails", {})
                            .get("relatedPlaylists", {}).get("uploads")),
                "video_count": int(st.get("videoCount", 0)),
            }
    return out


def channel_view_sample(uploads_playlist, key, sample):
    """{video_id: views} for the channel's recent uploads. ~2 units.

    Returned raw rather than pre-averaged so the caller can exclude the
    candidate video before taking a median. A candidate that is itself a
    large outlier would otherwise inflate the baseline it is measured
    against, understating the multiple.
    """
    if not uploads_playlist:
        return {}
    data = api_get("playlistItems", {
        "part": "contentDetails",
        "playlistId": uploads_playlist,
        "maxResults": min(sample, 50),
    }, key, cost=1)
    vids = [i["contentDetails"]["videoId"] for i in data.get("items", [])]
    if not vids:
        return {}
    return {vid: v["views"] for vid, v in fetch_videos(vids, key).items()
            if v["seconds"] >= 60}


def median_excluding(sample, video_id, minimum=4):
    """Median of the sample with video_id removed. None if too thin."""
    views = [v for vid, v in sample.items() if vid != video_id]
    return statistics.median(views) if len(views) >= minimum else None


def write_html(rows, path, args):
    css = """body{font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    margin:0;padding:32px;background:#0f1115;color:#e6e8eb}
    h1{font-size:20px;margin:0 0 6px} .sub{color:#8b9199;margin:0 0 28px;font-size:13px}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px}
    .card{background:#181b21;border:1px solid #262a33;border-radius:10px;overflow:hidden}
    .card img{width:100%;display:block;background:#000;aspect-ratio:16/9;object-fit:cover}
    .body{padding:12px 14px 14px} .t{font-weight:600;margin:0 0 8px;font-size:14px;
    line-height:1.35} .t a{color:#e6e8eb;text-decoration:none} .t a:hover{color:#7aa2f7}
    .m{color:#8b9199;font-size:12.5px;margin:2px 0} b{color:#9ece6a}
    .rank{color:#565c66;font-size:11px;letter-spacing:.08em;text-transform:uppercase}"""
    cards = []
    for i, r in enumerate(rows, 1):
        mult = f"{r['outlier_multiple']:.1f}x channel median" if r["outlier_multiple"] else "median n/a"
        subs = f"{r['subs']:,}" if r["subs"] is not None else "hidden"
        ratio_s = "ratio n/a" if r["ratio"] == float("inf") else f"ratio {r['ratio']:.1f}:1"
        cards.append(f"""<div class="card"><img src="{html.escape(r['thumb'])}" loading="lazy">
<div class="body"><span class="rank">#{i} &middot; {html.escape(r['keyword'])}</span>
<p class="t"><a href="https://youtu.be/{r['video_id']}" target="_blank">{html.escape(r['title'])}</a></p>
<p class="m">{html.escape(r['channel_title'])} &middot; {subs} subs</p>
<p class="m"><b>{r['views']:,} views</b> &middot; {ratio_s} &middot; {mult}</p>
<p class="m">{r['published_at']} &middot; {r['seconds'] // 60}m{r['seconds'] % 60:02d}s</p>
</div></div>""")
    doc = f"""<!doctype html><meta charset="utf-8"><title>Outlier scan results</title>
<style>{css}</style><h1>{len(rows)} outlier candidates</h1>
<p class="sub">Filters: {args.min_views:,}+ views &middot; under {args.max_subs:,} subs
&middot; ratio {args.min_ratio}:1+ &middot; {args.min_seconds}s+ runtime.
Ranked by channel-relative outlier multiple, falling back to views/subs ratio.
Packaging is your call: scan the thumbnails and flag the weak ones pulling big numbers.<br>\nView and subscriber counts are YouTube Data API values.\nRatio and outlier multiple are computed locally by this script.</p>
<div class="grid">{''.join(cards)}</div>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def main():
    p = argparse.ArgumentParser(
        description="Find outlier YouTube videos worth making your own version of.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("keywords", nargs="+", help="search phrases to scan")
    p.add_argument("--min-views", type=int, default=100_000,
                   help="video view floor (drop this hard for B2B niches)")
    p.add_argument("--max-subs", type=int, default=100_000,
                   help="channel subscriber ceiling")
    p.add_argument("--min-ratio", type=float, default=5.0,
                   help="minimum views-to-subscribers ratio")
    p.add_argument("--min-seconds", type=int, default=180,
                   help="runtime floor, excludes Shorts")
    p.add_argument("--months", type=int, default=24,
                   help="only videos published within this many months, 0 for all time")
    p.add_argument("--pages", type=int, default=1,
                   help="search pages per keyword, 50 results each, 100 units each")
    p.add_argument("--order", default="relevance",
                   choices=["relevance", "viewCount", "date", "rating"])
    p.add_argument("--region", default="US")
    p.add_argument("--median-sample", type=int, default=25,
                   help="recent uploads sampled for the channel median")
    p.add_argument("--allow-hidden-subs", action="store_true",
                   help="keep channels that hide their subscriber count")
    p.add_argument("--no-median", action="store_true",
                   help="skip channel-median enrichment to save a little quota")
    p.add_argument("--out", default="outlier_scan",
                   help="output basename, writes .csv and .html")
    args = p.parse_args()

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit("Set YOUTUBE_API_KEY in your environment first.")

    published_after = None
    if args.months:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30 * args.months)
        published_after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    for kw in args.keywords:
        print(f"searching: {kw}", file=sys.stderr)
        vids = search_videos(kw, key, args.pages, args.order,
                             published_after, args.region)
        if not vids:
            continue
        details = fetch_videos(vids, key)
        chans = fetch_channels([v["channel_id"] for v in details.values()], key)

        drops = {"found": len(details), "too_short": 0, "below_min_views": 0,
                 "hidden_subs": 0, "above_max_subs": 0, "below_min_ratio": 0,
                 "passed": 0}
        for v in details.values():
            c = chans.get(v["channel_id"], {})
            subs = c.get("subs")
            if v["seconds"] < args.min_seconds:
                drops["too_short"] += 1
                continue
            if v["views"] < args.min_views:
                drops["below_min_views"] += 1
                continue
            if subs is None:
                # Channel hides its subscriber count, so the ratio is
                # unknowable. Dropped unless explicitly asked for.
                if not args.allow_hidden_subs:
                    drops["hidden_subs"] += 1
                    continue
                ratio = float("inf")
            elif subs == 0:
                ratio = float("inf")
            else:
                if subs > args.max_subs:
                    drops["above_max_subs"] += 1
                    continue
                ratio = v["views"] / subs
            if ratio < args.min_ratio:
                drops["below_min_ratio"] += 1
                continue
            drops["passed"] += 1
            row = dict(v, keyword=kw, subs=subs, ratio=ratio,
                       uploads=c.get("uploads"), outlier_multiple=None)
            candidates.append(row)
        print("  funnel: " + "  ".join(f"{k}={n}" for k, n in drops.items()),
              file=sys.stderr)

    # De-duplicate across keywords, keeping the first hit.
    seen, rows = set(), []
    for r in candidates:
        if r["video_id"] not in seen:
            seen.add(r["video_id"])
            rows.append(r)

    if not args.no_median:
        samples = {}
        for r in rows:
            cid = r["channel_id"]
            if cid not in samples:
                print(f"  sampling {r['channel_title']}", file=sys.stderr)
                samples[cid] = channel_view_sample(
                    r["uploads"], key, args.median_sample)
            med = median_excluding(samples[cid], r["video_id"])
            if med:
                r["outlier_multiple"] = r["views"] / med

    rows.sort(key=lambda r: (r["outlier_multiple"] or 0, r["ratio"]), reverse=True)

    fields = ["keyword", "title", "video_id", "channel_title", "views", "subs",
              "ratio", "outlier_multiple", "published_at", "seconds",
              "likes", "comments", "thumb"]
    csv_path = args.out + ".csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    html_path = args.out + ".html"
    write_html(rows, html_path, args)

    print(f"\n{len(rows)} candidates passed.")
    print(f"  {csv_path}")
    print(f"  {html_path}  <- open this and judge the thumbnails")
    print(f"quota spent: {Quota.used} units of 10,000")


if __name__ == "__main__":
    main()
