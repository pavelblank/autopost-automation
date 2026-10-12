# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - analytics.py
Tracks content performance and provides adaptive scheduling recommendations.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db

ROOT = (Path(__file__).resolve().parents[1])


def log(m):
    try:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m, flush=True)
    except UnicodeEncodeError:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m.encode("ascii", "replace").decode("ascii"), flush=True)


def get_content_performance(days=7):
    """
    Analyze which content types perform best in last N days.
    Returns: dict with content type → average engagement score.
    """
    try:
        with db.conn() as c:
            # Get posts from last N days grouped by content type
            query = """
            SELECT r.kind, COUNT(*) as count,
                   AVG(CAST(COALESCE(p.views_48h, 0) AS FLOAT)) as avg_views
            FROM runs r
            LEFT JOIN posts p ON r.id = p.run_id
            WHERE r.published=1 AND r.started > ?
            GROUP BY r.kind
            ORDER BY avg_views DESC
            """
            cutoff = time.time() - (days * 86400)
            results = c.execute(query, (cutoff,)).fetchall()

            perf = {}
            for row in results:
                kind = row["kind"] or "text"
                avg_views = row["avg_views"] or 0
                count = row["count"] or 0
                perf[kind] = {"avg_views": avg_views, "count": count}

            return perf
    except Exception as e:
        log(f"  performance analysis failed: {str(e)[:100]}")
        return {}


def get_category_performance(days=7):
    """
    Analyze which content categories/tones perform best.
    Returns: dict with category → metrics.
    """
    try:
        with db.conn() as c:
            query = """
            SELECT r.category, r.tone, COUNT(*) as count,
                   AVG(CAST(COALESCE(p.views_48h, 0) AS FLOAT)) as avg_views
            FROM runs r
            LEFT JOIN posts p ON r.id = p.run_id
            WHERE r.published=1 AND r.started > ?
            GROUP BY r.category, r.tone
            ORDER BY avg_views DESC
            """
            cutoff = time.time() - (days * 86400)
            results = c.execute(query, (cutoff,)).fetchall()

            perf = {}
            for row in results:
                category = row["category"] or "wellness"
                tone = row["tone"] or "inspirational"
                key = f"{category}_{tone}"
                perf[key] = {
                    "category": category,
                    "tone": tone,
                    "avg_views": row["avg_views"] or 0,
                    "count": row["count"] or 0
                }

            return perf
    except Exception as e:
        log(f"  category analysis failed: {str(e)[:100]}")
        return {}


def recommend_content_mix(days=7):
    """
    Based on performance, recommend content generation mix.
    Returns: (text_ratio, image_ratio, video_ratio) - sum to 1.0
    """
    perf = get_content_performance(days)

    if not perf:
        # Default balanced mix
        return (0.35, 0.35, 0.30)

    # Calculate adjusted ratios based on performance
    total_views = sum(p.get("avg_views", 0) for p in perf.values())

    if total_views == 0:
        return (0.35, 0.35, 0.30)  # Default if no data

    ratios = {}
    for kind in ["text", "image", "video"]:
        if kind in perf:
            views = perf[kind].get("avg_views", 0)
            ratio = views / total_views if total_views > 0 else 0.33
            ratios[kind] = max(0.1, min(0.6, ratio))  # Clamp 10-60%
        else:
            ratios[kind] = 0.33 / 3

    # Normalize to sum to 1.0
    total = sum(ratios.values())
    text_ratio = ratios.get("text", 0.33) / total
    image_ratio = ratios.get("image", 0.33) / total
    video_ratio = ratios.get("video", 0.33) / total

    return (text_ratio, image_ratio, video_ratio)


def report_analytics(days=7):
    """
    Print analytics report for user viewing.
    """
    log(f"📊 ANALYTICS (Last {days} days)")
    log("-" * 50)

    perf = get_content_performance(days)
    if perf:
        log("Content Performance:")
        for kind in ["video", "image", "text"]:
            if kind in perf:
                p = perf[kind]
                count = p.get("count", 0)
                avg_views = p.get("avg_views", 0)
                log(f"  {kind:6s}: {int(avg_views):6d} avg views ({int(count)} posts)")

    mix = recommend_content_mix(days)
    log("")
    log("Recommended Content Mix:")
    log(f"  Text:  {mix[0]:.0%}")
    log(f"  Image: {mix[1]:.0%}")
    log(f"  Video: {mix[2]:.0%}")

    cat_perf = get_category_performance(days)
    if cat_perf:
        log("")
        log("Top Performing Content:")
        for key, item in sorted(cat_perf.items(), key=lambda x: x[1]["avg_views"], reverse=True)[:5]:
            log(f"  {item['category']:12s} + {item['tone']:12s}: {int(item['avg_views']):6d} views")


if __name__ == "__main__":
    report_analytics(7)
