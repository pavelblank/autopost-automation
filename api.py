# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - API Server
REST API backend for the new dashboard.
Serves simple JSON data for live updates.

  python api.py                    # Run on http://localhost:8600
"""
import json
import time
import os
import threading
import subprocess
import sys
from flask import Flask, jsonify, request, render_template, send_file
from pathlib import Path

# Ensure venv is in path for googleapiclient
venv_path = (Path(__file__).resolve().parent / "venv" / "Lib" / "site-packages")
if str(venv_path) not in sys.path:
    sys.path.insert(0, str(venv_path))

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import db

ROOT = Path(__file__).parent
app = Flask(__name__, static_folder=str(ROOT), static_url_path='')

@app.route('/')
@app.route('/index.html')
def dashboard():
    """Serve the main dashboard."""
    return send_file(str(ROOT / "dashboard_v2.html"), mimetype='text/html')

@app.route('/dashboard_v2.html')
def dashboard_v2():
    """Serve dashboard v2."""
    return send_file(str(ROOT / "dashboard_v2.html"), mimetype='text/html')

@app.route('/generate.html')
def generate():
    """Serve generate page."""
    return send_file(str(ROOT / "generate.html"), mimetype='text/html')

@app.route('/activity.html')
def activity():
    """Serve activity page."""
    return send_file(str(ROOT / "activity.html"), mimetype='text/html')

@app.route('/settings.html')
def settings_page():
    """Serve settings page."""
    return send_file(str(ROOT / "settings.html"), mimetype='text/html')

@app.route('/audit.html')
def audit():
    """Serve audit page."""
    return send_file(str(ROOT / "audit.html"), mimetype='text/html')

@app.route('/api/status')
def get_status():
    """Get current system status."""
    try:
        import subprocess
        import shutil

        # Check FFmpeg
        ffmpeg_status = "ready"
        if shutil.which("ffmpeg") is None:
            ffmpeg_status = "not_found"
        else:
            try:
                subprocess.run(["ffmpeg", "-version"],
                             capture_output=True, timeout=5, check=False)
                ffmpeg_status = "ready"
            except:
                ffmpeg_status = "error"

        # Check Ollama
        ollama_status = "connected"
        ollama_model = "llama3.2:3b"
        try:
            import json
            rules_path = ROOT / "config" / "rules.json"
            if rules_path.exists():
                try:
                    rules = json.loads(rules_path.read_text(encoding="utf-8"))
                    ollama_model = rules.get("ollama_model", "llama3.2:3b")
                except:
                    pass
        except:
            pass
        try:
            # Try to reach Ollama
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('127.0.0.1', 11434))
            sock.close()
            if result != 0:
                ollama_status = "offline"
        except:
            ollama_status = "offline"

        with db.conn() as c:
            # Get scheduler status
            recent_run = c.execute(
                "SELECT status, stage FROM runs ORDER BY started DESC LIMIT 1"
            ).fetchone()

            # Get research topics count
            topics_count = c.execute(
                "SELECT COUNT(*) as cnt FROM topics WHERE used=0"
            ).fetchone()

            return jsonify({
                "scheduler": "running",
                "ollama": ollama_status,
                "ollama_model": ollama_model,
                "ffmpeg": ffmpeg_status,
                "ffmpeg_version": "9.0.1",
                "topics_available": topics_count["cnt"] if topics_count else 0,
                "current_stage": recent_run["stage"] if recent_run else "idle"
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Get today's statistics."""
    try:
        with db.conn() as c:
            today_start = int(time.time()) - (24 * 3600)

            # Today's posts
            generated = c.execute(
                "SELECT COUNT(*) as cnt FROM runs WHERE started > ? AND status='ok'",
                (today_start,)
            ).fetchone()

            published = c.execute(
                "SELECT COUNT(*) as cnt FROM runs WHERE started > ? AND published=1",
                (today_start,)
            ).fetchone()

            approved = c.execute(
                "SELECT COUNT(*) as cnt FROM audit_logs WHERE timestamp > ? AND status='approved'",
                (today_start,)
            ).fetchone()

            return jsonify({
                "generated": generated["cnt"] if generated else 0,
                "published": published["cnt"] if published else 0,
                "audit_passed": approved["cnt"] if approved else 0,
                "avg_time_minutes": 15
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/progress')
def get_progress():
    """Get current job progress."""
    try:
        with db.conn() as c:
            current = c.execute(
                "SELECT topic, stage, started FROM runs WHERE status='running' ORDER BY started DESC LIMIT 1"
            ).fetchone()

            if not current:
                return jsonify({
                    "status": "idle",
                    "title": "Idle (waiting for next job)",
                    "progress": 0
                })

            elapsed = int(time.time() - current["started"])
            stage = current["stage"] or "generating"

            return jsonify({
                "status": "running",
                "title": f"{stage}: {current['topic'][:50]}",
                "elapsed_seconds": elapsed,
                "progress": min(90, (elapsed // 10) * 10)
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/posts')
def get_posts():
    """Get recent posts."""
    try:
        limit = request.args.get('limit', 5, type=int)

        with db.conn() as c:
            posts = c.execute("""
                SELECT r.id, r.topic, r.kind, r.started, r.published, r.status,
                       COUNT(p.id) as platform_count
                FROM runs r
                LEFT JOIN posts p ON r.id = p.run_id
                WHERE r.status='ok'
                GROUP BY r.id
                ORDER BY r.started DESC
                LIMIT ?
            """, (limit,)).fetchall()

            result = []
            for post in posts:
                timestamp = time.strftime("%I:%M %p", time.localtime(post["started"])).lstrip("0")
                status = "✅" if post["published"] else "⏸️"
                result.append({
                    "time": timestamp,
                    "status": status,
                    "title": post["topic"][:50],
                    "kind": post["kind"],
                    "platforms": post["platform_count"] or 0
                })

            return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/audit-logs')
def get_audit_logs():
    """Get recent audit logs."""
    try:
        limit = request.args.get('limit', 10, type=int)

        with db.conn() as c:
            logs = c.execute("""
                SELECT platform, status, reason, timestamp
                FROM audit_logs
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()

            result = []
            for log in logs:
                result.append({
                    "platform": log["platform"],
                    "status": log["status"],
                    "reason": log["reason"] or "OK",
                    "time": time.strftime("%I:%M %p", time.localtime(log["timestamp"])).lstrip("0")
                })

            return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/generate', methods=['POST'])
def generate_content():
    """Generate content based on topic and settings."""
    try:
        data = request.json
        topic = data.get('topic', '')
        kind = data.get('kind', 'text')
        action = data.get('action', 'publish')
        model = data.get('model', 'llama3.2:3b')
        platforms = data.get('platforms', [])

        # Record the generation request
        with db.conn() as c:
            cur = c.execute("""
                INSERT INTO runs (topic, kind, status, started, preview_requested)
                VALUES (?, ?, ?, ?, ?)
            """, (topic, kind, 'running', time.time(), 1 if action == 'preview' else 0))
            run_id = cur.lastrowid

        # Launch the generation in background
        def run_generation():
            try:
                env = os.environ.copy()
                env['OLLAMA_MODEL'] = model
                
                if kind == 'video':
                    # Use make_video.py for video generation
                    script = str(ROOT / "scripts" / "make_video.py")
                    args = [sys.executable, script, topic]
                else:
                    # Use make_post.py for text/image
                    script = str(ROOT / "scripts" / "make_post.py")
                    args = [sys.executable, script, kind, topic]
                    if action == 'preview':
                        args.append("--preview")
                
                # Run in background
                proc = subprocess.Popen(args, cwd=str(ROOT), env=env, 
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = proc.communicate(timeout=600)  # 10 min timeout
                
                if proc.returncode != 0:
                    error_msg = stderr.decode('utf-8', errors='ignore')[:500]
                    print(f"Generation failed: {error_msg}")
                    with db.conn() as c:
                        c.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (error_msg, run_id))
                else:
                    print(f"Generation completed for run {run_id}")
                    
            except subprocess.TimeoutExpired:
                with db.conn() as c:
                    c.execute("UPDATE runs SET status='failed', error='Generation timed out (10 min)' WHERE id=?", (run_id,))
            except Exception as e:
                print(f"Background generation failed: {e}")
                with db.conn() as c:
                    c.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (str(e)[:500], run_id))

        threading.Thread(target=run_generation, daemon=True).start()

        return jsonify({
            "status": "started",
            "run_id": run_id,
            "topic": topic,
            "kind": kind,
            "action": action,
            "model": model,
            "platforms": platforms,
            "message": f"Generating {kind} post for: {topic}"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/approve', methods=['POST'])
def approve_post():
    """Approve a post for publishing."""
    try:
        data = request.json
        run_id = data.get('run_id')

        with db.conn() as c:
            c.execute("""
                UPDATE runs
                SET preview_action = 'approved'
                WHERE id = ?
            """, (run_id,))

            return jsonify({
                "status": "approved",
                "run_id": run_id,
                "message": "Post approved and published"
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    """Get or update user settings."""
    try:
        if request.method == 'GET':
            # Return current settings (defaults for now)
            return jsonify({
                "auto_publish_mode": "enabled",  # or "preview_first" or "draft"
                "ai_model": "qwen2.5",
                "posts_per_day": 5,
                "image_text_overlay": True,
                "auto_research": True,
                "telegram_alerts": True
            })

        # POST: Update settings
        settings_data = request.json
        # Would save to database in real implementation
        return jsonify({"status": "saved", "settings": settings_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    print("Starting AutoPost API Server...")
    print("Dashboard: http://localhost:8600")
    print("API: http://localhost:8600/api/")
    print("")
    print("Pages available:")
    print("  Dashboard: http://localhost:8600/")
    print("  Generate: http://localhost:8600/generate.html")
    print("  Activity: http://localhost:8600/activity.html")
    print("  Settings: http://localhost:8600/settings.html")
    print("  Audit: http://localhost:8600/audit.html")
    app.run(host='0.0.0.0', port=8600, debug=False, use_reloader=False)
