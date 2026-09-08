# youtube-outlier-scan

Finds outlier YouTube videos: high views relative to channel size, ranked against each channel's own median. Zero-dependency Python CLI on the YouTube Data API.

```
$ python3 outlier_scan.py \
    "GRC analyst" "ISO 27001 implementation" "SOC 2 compliance" \
    "NIST cybersecurity framework" "how to become a GRC analyst" \
    --min-views 3000 --max-subs 30000 --min-ratio 3 --months 36 --no-median

searching: GRC analyst
  funnel: found=50  too_short=9  below_min_views=8  hidden_subs=0  above_max_subs=18  below_min_ratio=8  passed=7
searching: ISO 27001 implementation
  funnel: found=50  too_short=21  below_min_views=9  hidden_subs=0  above_max_subs=16  below_min_ratio=3  passed=1
searching: SOC 2 compliance
  funnel: found=50  too_short=28  below_min_views=15  hidden_subs=0  above_max_subs=3  below_min_ratio=2  passed=2
searching: NIST cybersecurity framework
  funnel: found=50  too_short=17  below_min_views=15  hidden_subs=0  above_max_subs=9  below_min_ratio=3  passed=6
searching: how to become a GRC analyst
  funnel: found=50  too_short=17  below_min_views=8  hidden_subs=0  above_max_subs=15  below_min_ratio=7  passed=3

16 candidates passed.
  outlier_scan.csv
  outlier_scan.html  <- open this and judge the thumbnails
quota spent: 510 units of 10,000
```

The top rows of `outlier_scan.csv` from that run, sorted by views-to-subscribers
ratio (the `--no-median` run leaves the outlier multiple blank):

| views | subs | ratio | channel | title |
|---|---|---|---|---|
| 35,621 | 695 | 51.3 | Winslow Technology Group | Exploring the NIST Cybersecurity Framework 2.0: What You Need to Know |
| 5,574 | 153 | 36.4 | TechStack with Josue | GRC Analyst vs SOC Analyst vs Pentester |
| 10,894 | 457 | 23.8 | Digital Nova Scotia | NIST Cybersecurity Framework: A Beginner's Guide |
| 13,149 | 606 | 21.7 | Mindset Cyber | ISO 27001 Checklist \| Step-by-Step Guide to Build a Compliant ISMS |
| 15,755 | 923 | 17.1 | Christian Khoury | SOC 2 Compliance: Everything You Need to Know in 2026 |

This is a narrow niche: the winners top out around 15k-40k views, not the
100k the defaults assume, which is why the thresholds are scaled down. See
[Tuning for your niche](#tuning-for-your-niche).

## Why this exists

A video from a 4,000-subscriber channel that pulled 300,000 views did not get there on production value. The idea carried it. That gap between a topic's demand and the quality of what currently serves it is the thing worth finding, because you can make a better version of a proven idea far more reliably than you can invent a new one.

Doing this by hand means running searches, opening channel pages, and doing arithmetic in your head across dozens of tabs. This does it in one command.

## What it screens for

Three criteria run mechanically:

| Criterion | Flag | Default |
|---|---|---|
| Video views above a floor | `--min-views` | 100,000 |
| Channel subscribers below a ceiling | `--max-subs` | 100,000 |
| Views-to-subscribers ratio | `--min-ratio` | 5.0 |

A fourth criterion, packaging quality, is a judgment call and the script does not try to automate it. Instead it writes an HTML contact sheet so you can scan every surviving thumbnail at once and flag the weak ones pulling big numbers.

### The outlier multiple

Views-over-subscribers is a blunt instrument. It flatters new channels that haven't accumulated subscribers yet and penalises channels whose audience simply hasn't converted.

So the script also computes an **outlier multiple**: this video's views divided by the median views of the channel's other recent uploads. A video at 8x its own channel's median found something, whatever the subscriber count says. Results are ranked by this figure, falling back to the raw ratio when a channel has too few uploads to establish a baseline.

The candidate video is excluded from its own median. A channel's uploads playlist contains the video you're scoring, and leaving it in would inflate the baseline it's measured against.

## Requirements

- Python 3.8 or newer
- A YouTube Data API v3 key
- No third-party packages. Standard library only, so no virtualenv needed unless you want one.

## Getting an API key

The key is a Google Cloud credential, not a YouTube account credential. It reads public data only, requires no OAuth, and cannot touch your channel. No billing account or card is required.

1. **Create a project.** Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project, e.g. `yt-research`. Quota is allocated per project, so keeping this separate means a runaway loop here can't starve anything else you run.
2. **Enable the API.** APIs & Services → Library → search "YouTube Data API v3" → Enable. Skipping this is the most common first failure: the key looks valid and every call returns `403 accessNotConfigured` until the API is switched on.
3. **Create the key.** APIs & Services → Credentials → Create Credentials → API key. Ignore any prompts about OAuth consent screens or service accounts; neither applies here.
4. **Restrict it.** Click into the key, set API restrictions to "Restrict key", select only YouTube Data API v3. Leave application restrictions set to None, since IP and referrer restrictions break CLI use.
5. **Put it in your environment.**

   ```bash
   export YOUTUBE_API_KEY="your-key-here"
   ```

   Add it to your shell profile, or use a `.env` file with `.gitignore` updated **before** you create the file.

## Usage

```bash
python3 outlier_scan.py "keyword one" "keyword two" [options]
```

| Flag | Default | Notes |
|---|---|---|
| `--min-views` | 100000 | Lower this hard for small niches. See tuning below. |
| `--max-subs` | 100000 | Channel subscriber ceiling. |
| `--min-ratio` | 5.0 | Views-to-subscribers ratio floor. |
| `--min-seconds` | 180 | Runtime floor. Excludes Shorts, which now run up to 3 minutes. |
| `--months` | 24 | Only videos published this recently. `0` for all time. |
| `--pages` | 1 | Search pages per keyword, 50 results each. **Costs 100 units per page.** |
| `--order` | relevance | `relevance`, `viewCount`, `date`, `rating`. |
| `--region` | US | Region code for search results. |
| `--median-sample` | 25 | Recent uploads sampled for the channel baseline. |
| `--allow-hidden-subs` | off | Keep channels that hide their subscriber count. Their ratio is unknowable, so they're dropped by default. |
| `--no-median` | off | Skip baseline enrichment. Use on your first run. |
| `--out` | outlier_scan | Output basename. Writes `.csv` and `.html`. |

### Tuning for your niche

**The defaults will return nothing in a small niche, and that is not a signal that the niche is dead.**

The 100,000-view threshold assumes a broad consumer topic. In a narrow B2B or technical niche, a genuinely excellent video might top out at 20,000 views because the total addressable audience is small. Scale the absolute numbers down and keep the ratio:

```bash
# broad consumer niche
python3 outlier_scan.py "meal prep for beginners"

# narrow technical niche
python3 outlier_scan.py "kubernetes operator tutorial" \
  --min-views 5000 --max-subs 25000 --min-ratio 3
```

A video with 25,000 views from a 2,000-subscriber channel is the same signal as 250,000 from 20,000. Different altitude, same shape.

### Reading the funnel

For each keyword the script prints a `funnel:` line to stderr showing how the 50 search results were whittled down:

```
searching: GRC analyst
  funnel: found=50  too_short=9  below_min_views=8  hidden_subs=0  above_max_subs=18  below_min_ratio=8  passed=7
```

Each count is the number of videos dropped at that gate, in order, with `passed` the number that cleared all of them. When a scan returns nothing, this tells you which threshold to move:

| Dominant count | What it means | What to change |
|---|---|---|
| `too_short` | the keyword returns mostly Shorts or clips | rephrase toward long-form, or lower `--min-seconds` |
| `below_min_views` | the niche's ceiling is below your floor | lower `--min-views` |
| `above_max_subs` | the high-view videos are all from big channels | lower `--min-views` so smaller channels enter the pool; raising `--max-subs` rarely helps |
| `below_min_ratio` | the outliers here just aren't dramatic | lower `--min-ratio` |
| `hidden_subs` | channels hiding their subscriber count | pass `--allow-hidden-subs` if you want them, accepting an unknowable ratio |

## Output

**`outlier_scan.csv`** — one row per surviving candidate: keyword, title, video ID, channel, views, subscribers, ratio, outlier multiple, publish date, runtime, likes, comments, thumbnail URL.

**`outlier_scan.html`** — a thumbnail contact sheet, ranked, each card linking to the video. Open this first. Judging 30 thumbnails laid out in a grid takes about a minute; judging them in 30 browser tabs does not.

## Quota

The default allocation is 10,000 units per day per Google Cloud project, resetting at midnight Pacific rather than your local midnight. Quota cannot be purchased; the only route to more is Google's quota extension request form, which is free but slow and not guaranteed.

| Call | Cost | Per |
|---|---|---|
| `search.list` | 100 | page of 50 results |
| `videos.list` | 1 | batch of 50 IDs |
| `channels.list` | 1 | batch of 50 IDs |
| `playlistItems.list` | 1 | batch of 50 items |

Searches dominate everything else. One keyword with baseline enrichment costs roughly 105 units, so you get about 90 keyword-scans a day. The script prints its total spend on exit.

**Make your first run cheap.** One keyword, `--no-median`, and confirm the CSV looks plausible before spending more:

```bash
python3 outlier_scan.py "your keyword" --no-median
```

## Tests

```bash
python3 test_outlier_scan.py
```

Runs offline with no key and no network. It fakes the API at the `urlopen` layer rather than stubbing functions, so URL construction, request batching, JSON parsing, filtering, ranking, quota accounting, and HTML rendering are all exercised.

The suite does not talk to YouTube, so its fixtures encode assumptions about the response shapes. Those assumptions have been checked once against a live key: `search.list` with `part=id`, and the field paths for view count, subscriber count, duration, the uploads playlist, and thumbnail URL all resolved as the fixtures expect. If YouTube moves a field later, the tests will still pass while the script fails, so treat a green suite as necessary but not sufficient after an API change.

## YouTube API policy notes

Two things worth knowing if you build on this.

**Stored data expires.** The [Developer Policies](https://developers.google.com/youtube/terms/developer-policies) require that stored API data be deleted or refreshed after 30 calendar days, and that clients make reasonable efforts to keep stored data consistent with what's currently live. Treat any CSV this produces as a snapshot. Rerun rather than trusting an old file.

**Ratio and outlier multiple are derived metrics.** Google's guidance is that developers may compute custom scores from averages, sums, or ratios of API data where the result adds analytical value, but must not redisplay raw API data under a new name or misrepresent its provenance. This script keeps that line visible: view and subscriber counts are labelled as API values, while the ratio and outlier multiple are computed locally and labelled as such. Preserve that distinction if you fork it.

## Credit

The screening approach is adapted from the video-idea method that [Shane Hummus](https://shanehummus.com/youtube-growth/how-to-spot-viral-video-ideas-for-your-youtube-channel/) calls the Icahn Method, which looks for proven demand by finding videos that outperformed the channel that published them. This project is not affiliated with or endorsed by him. The channel-relative outlier multiple, the runtime filter, and everything else here are additions.

## License

MIT. See [LICENSE](LICENSE).

Provided as is. You run it against your own API key and your own daily quota.
