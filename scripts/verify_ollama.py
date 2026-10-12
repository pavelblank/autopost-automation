# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""AutoPost - verify_ollama.py
Waits up to ~75s for Ollama to come up AND pass a real generation probe,
then prints a clear verdict. Used by FIX_ALL.bat (admin recovery) and by hand.
Exit 0 = healthy, exit 1 = still broken."""
import sys, time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_health

def main():
    print("waiting for Ollama to answer a REAL generation...")
    for i in range(15):
        ok = ai_health.ollama_ok(timeout=10)
        if ok:
            print("OLLAMA GENERATES OK - model healthy (probe: '%s')" % ok[:30])
            return 0
        m = ai_health.ollama_models()
        print("probe %d failed (models listed: %d), waiting 5s..." % (i + 1, len(m)))
        time.sleep(5)
    print(ai_health.human_status())
    print("OLLAMA STILL BROKEN after %ds - run FIX_ALL.bat as admin again" % (15 * 5))
    return 1

if __name__ == "__main__":
    sys.exit(main())