# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Trigger a single AutoPost generation job with research topic."""
import sys, json, time, random
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import db

# Get a topic from the database
conn = db.conn()
c = conn.cursor()
c.execute('SELECT title FROM topics ORDER BY RANDOM() LIMIT 5')
topics = [r[0] for r in c.fetchall()]
print('Available topics:', len(topics))
for t in topics[:3]:
    print(f'  - {t}')

if not topics:
    print('ERROR: No topics found in database')
    sys.exit(1)

topic = topics[0]
print(f'\nSelected topic: {topic}')

# Start a generation run
run_id = db.start_run(topic=topic, kind='video')
print(f'Run ID: {run_id}')

# Generate text + image
print('\n[1/3] Generating text...')
db.set_stage(run_id, 'text')
# Text generation happens via the scheduler, but we can trigger it directly
# For now, just mark as ready for the scheduler to pick up
db.set_stage(run_id, 'ready')

print('[2/3] Image generation - will be picked up by scheduler')
print('[3/3] Video generation - will be picked up by scheduler')
print(f'\nRun {run_id} is queued. The scheduler will process it.')
print(f'Check dashboard at http://localhost:8600 for progress')
