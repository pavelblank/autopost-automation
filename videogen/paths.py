# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""videogen/paths.py - THE single source of truth for every path in the Video Department.

Every agent and every script imports THIS. Never hard-code a path anywhere else.
If a folder moves, one edit here fixes the whole system.

    from pathlib import Path
    import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "videogen"))
    from paths import WORK, FINAL, OFFICE, room, work_area
"""
from pathlib import Path

VG = Path(__file__).resolve().parent          # videogen/
ROOT = VG.parent                              # Autopost/

# ---------------------------------------------------------------- the four homes
OFFICE = VG / "OFFICE"          # 18 rooms, one per agent. Each owns its own STATUS.md.
WORK = VG / "WORK"              # ALL generated work. Nothing finished lives here.
FINAL = VG / "FINAL"            # finished deliverables ONLY.
LOGS = VG / "logs"

# ---------------------------------------------------------------- code
ENGINE = VG / "ENGINE"          # Wan2.1 T2V + clip generation
EDIT = VG / "EDIT"              # narration, captions, upscale, loudness, mux
TOOLS = VG / "TOOLS"            # orchestrators + verification
LIBRARY = VG / "SCREENPLAYS"    # reusable screenplay templates (not per-job)

# ---------------------------------------------------------------- WORK sub-areas
AREA_INTAKE = WORK / "01-intake"
AREA_SCRIPTS = WORK / "02-scripts"
AREA_RESEARCH = WORK / "03-research"
AREA_VISUAL = WORK / "04-visual"
AREA_CLIPS = WORK / "05-clips"
AREA_VOICE = WORK / "06-voice"
AREA_SEGMENTS = WORK / "07-segments"
AREA_CAPTIONS = WORK / "08-captions"
AREA_PACKAGES = WORK / "09-packages"
AREA_AUDITS = WORK / "10-audits"
AREA_EVIDENCE = WORK / "11-evidence"
SCRATCH = WORK / "_scratch"     # ffmpeg temp. Safe to empty at any time.

AREAS = {
    "intake": AREA_INTAKE, "scripts": AREA_SCRIPTS, "research": AREA_RESEARCH,
    "visual": AREA_VISUAL, "clips": AREA_CLIPS, "voice": AREA_VOICE,
    "segments": AREA_SEGMENTS, "captions": AREA_CAPTIONS,
    "packages": AREA_PACKAGES, "audits": AREA_AUDITS, "evidence": AREA_EVIDENCE,
}

# ---------------------------------------------------------------- the 18 rooms
#  key        room folder        agent   role                                  stage        writes to
ROOMS = {
    "intake":    ("01-intake",    None,    "Intake Controller",                 1, "intake"),
    "maya":      ("02-maya",      "MAYA",     "Creative Strategist",            2, "scripts"),
    "arjun":     ("03-arjun",     "ARJUN",    "Research Specialist",             2, "research"),
    "sophie":    ("04-sophie",    "SOPHIE",   "Scriptwriter",                    2, "scripts"),
    "daniel":    ("05-daniel",    "DANIEL",   "Storyboard Director",             2, "scripts"),
    "aisha":     ("06-aisha",     "AISHA",    "Visual Design Specialist",        2, "visual"),
    "leo":       ("07-leo",       "LEO",      "Emotion and Performance Director", 2, "visual"),
    "oliver":    ("08-oliver",    "OLIVER",   "Planning Supervisor",             3, "audits"),
    "isabella":  ("09-isabella",  "ISABELLA", "Senior Production Supervisor",    3, "audits"),
    "noah":      ("10-noah",      "NOAH",     "Media Production Operator",       4, "clips"),
    "grace":     ("11-grace",     "GRACE",    "Media Quality Inspector",         4, "audits"),
    "ethan":     ("12-ethan",     "ETHAN",    "Final Content Auditor",           4, "audits"),
    "chloe":     ("13-chloe",     "CHLOE",    "Platform Formatting Specialist",  5, "packages"),
    "marcus":    ("14-marcus",    "MARCUS",   "Release Manager",                 5, "packages"),
    "ella":      ("15-ella",      "ELLA",     "Publishing Operator",             5, "evidence"),
    "victoria":  ("16-victoria",  "VICTORIA", "Live Post Auditor",               6, "evidence"),
    "james":     ("17-james",     "JAMES",    "Incident and Recovery Manager",   6, "evidence"),
    "helena":    ("18-helena",    "HELENA",   "Department Head + Telegram",      7, "audits"),
}

STAGE_NAME = {1: "INPUT CLASSIFICATION", 2: "CREATIVE PLANNING AND PRODUCTION DESIGN",
              3: "PLANNING APPROVAL", 4: "GENERATION AND QUALITY CONTROL",
              5: "PLATFORM PREPARATION AND RELEASE", 6: "LIVE POST VERIFICATION",
              7: "FINAL REPORTING"}


def room(agent_key):
    """The agent's own room folder, created if missing."""
    if agent_key not in ROOMS:
        raise KeyError("unknown agent %r (have: %s)" % (agent_key, ", ".join(ROOMS)))
    d = OFFICE / ROOMS[agent_key][0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_area(area):
    """A WORK sub-area by short name, created if missing."""
    if area not in AREAS:
        raise KeyError("unknown area %r (have: %s)" % (area, ", ".join(AREAS)))
    AREAS[area].mkdir(parents=True, exist_ok=True)
    return AREAS[area]


def job_dir(job_id, area):
    """One job's folder inside a WORK area: WORK/<area>/<job_id>/"""
    d = work_area(area) / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_all():
    for d in (WORK, FINAL, LOGS, OFFICE):
        d.mkdir(parents=True, exist_ok=True)
    for a in AREAS.values():
        a.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for key in ROOMS:
        room(key)


def tree():
    """Print the whole department so an agent can see where it stands."""
    ensure_all()
    print("VIDEO DEPARTMENT")
    print("=" * 68)
    for stage in range(1, 8):
        keys = [k for k, v in ROOMS.items() if v[3] == stage]
        print("\nSTAGE %d - %s" % (stage, STAGE_NAME[stage]))
        for k in keys:
            folder, agent, role, _, area = ROOMS[k]
            print("   %-9s %-16s %-34s -> WORK/%s/"
                  % (agent or "(system)", folder, role, area))
    print("\n" + "=" * 68)
    print("WORK areas :", ", ".join(sorted(AREAS)))
    print("FINAL      :", FINAL)
    print("scratch    :", SCRATCH, "(safe to empty)")


if __name__ == "__main__":
    tree()
