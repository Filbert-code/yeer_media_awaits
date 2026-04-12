import json
import os
import traceback

from flask import Flask, jsonify, redirect, render_template, request, url_for
import qbittorrentapi

from media_library import MEDIA_LIBRARY

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CONFIG_EXAMPLE_PATH = os.path.join(BASE_DIR, "config.example.json")

DEFAULT_CONFIG = {
    "qbittorrent": {
        "host": "localhost",
        "port": 8080,
        "username": "admin",
        "password": "adminadmin",
    },
    "download_path": "",
}


def load_config():
    if not os.path.exists(CONFIG_PATH) and os.path.exists(CONFIG_EXAMPLE_PATH):
        import shutil
        shutil.copy(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "qbittorrent": {
                "host": "localhost",
                "port": 8080,
                "username": "admin",
                "password": "adminadmin",
            },
            "download_path": "",
        }


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get_qbt_client():
    config = load_config()
    qbt = config["qbittorrent"]
    return qbittorrentapi.Client(
        host=qbt["host"],
        port=qbt["port"],
        username=qbt["username"],
        password=qbt["password"],
    )


def format_size(size_bytes):
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"


def format_speed(speed_bytes):
    return f"{format_size(speed_bytes)}/s"


def format_eta(seconds):
    if seconds < 0 or seconds == 8640000:
        return "∞"
    if seconds == 0:
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{int(hours)}h {int(minutes)}m"
    if minutes > 0:
        return f"{int(minutes)}m {int(secs)}s"
    return f"{int(secs)}s"


def torrent_state_label(state):
    state_map = {
        "error": ("Dead in the Water", "error"),
        "missingFiles": ("Lost at Sea", "error"),
        "uploading": ("Sharin' the Bounty", "seeding"),
        "pausedUP": ("Anchored (Seeded)", "paused"),
        "stoppedUP": ("Anchored (Seeded)", "paused"),
        "queuedUP": ("Awaitin' Orders", "queued"),
        "stalledUP": ("Sharin' the Bounty", "seeding"),
        "checkingUP": ("Inspectin' the Cargo", "checking"),
        "forcedUP": ("Full Sail Sharin'", "seeding"),
        "allocating": ("Preparin' the Hold", "checking"),
        "downloading": ("Plunderin'", "downloading"),
        "metaDL": ("Scoutin' Ahead", "downloading"),
        "forcedMetaDL": ("Scoutin' Ahead", "downloading"),
        "pausedDL": ("Anchored", "paused"),
        "stoppedDL": ("Anchored", "paused"),
        "queuedDL": ("Awaitin' Orders", "queued"),
        "stalledDL": ("Becalmed", "stalled"),
        "checkingDL": ("Inspectin' the Cargo", "checking"),
        "forcedDL": ("Full Sail Plunderin'", "downloading"),
        "checkingResumeData": ("Checkin' the Maps", "checking"),
        "moving": ("Movin' the Loot", "checking"),
    }
    label, css_class = state_map.get(state, (state, "unknown"))
    return label, css_class


# ─── Pages ───────────────────────────────────────────────────────────

@app.route("/")
def downloads():
    return render_template("downloads.html")


@app.route("/browse")
def browse():
    query = request.args.get("q", "").strip().lower()
    category = request.args.get("category", "").strip().lower()

    filtered = MEDIA_LIBRARY
    if query:
        filtered = [
            m for m in filtered
            if query in m.get("title", "").lower()
            or query in m.get("description", "").lower()
        ]
    if category:
        filtered = [m for m in filtered if m.get("category", "").lower() == category]

    categories = sorted(set(m.get("category", "") for m in MEDIA_LIBRARY if m.get("category")))
    return render_template(
        "browse.html",
        media=filtered,
        categories=categories,
        query=request.args.get("q", ""),
        selected_category=category,
    )


@app.route("/settings")
def settings():
    config = load_config()
    return render_template("settings.html", config=config)


# ─── API Endpoints ───────────────────────────────────────────────────

@app.route("/api/torrents")
def api_torrents():
    try:
        client = get_qbt_client()
        torrents = client.torrents_info()
        result = []
        for t in torrents:
            label, css_class = torrent_state_label(t.state)
            result.append({
                "hash": t.hash,
                "name": t.name,
                "progress": round(t.progress * 100, 1),
                "state": t.state,
                "state_label": label,
                "state_class": css_class,
                "size": format_size(t.size),
                "downloaded": format_size(t.downloaded),
                "dl_speed": format_speed(t.dlspeed),
                "up_speed": format_speed(t.upspeed),
                "eta": format_eta(t.eta),
                "ratio": round(t.ratio, 2),
            })
        result.sort(key=lambda x: (
            0 if x["state_class"] == "downloading" else
            1 if x["state_class"] == "stalled" else
            2 if x["state_class"] == "checking" else
            3 if x["state_class"] == "queued" else
            4 if x["state_class"] == "seeding" else
            5 if x["state_class"] == "paused" else
            6
        ))
        return jsonify({"status": "ok", "torrents": result})
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Can't reach qBittorrent! Is the ship still afloat?",
            "details": traceback.format_exc(),
        }), 500


@app.route("/api/torrents/add", methods=["POST"])
def api_add_torrent():
    try:
        data = request.get_json()
        url = data.get("url", "")
        if not url:
            return jsonify({"status": "error", "message": "No treasure map provided!"}), 400

        client = get_qbt_client()
        config = load_config()
        kwargs = {"urls": url}
        if config.get("download_path"):
            kwargs["save_path"] = config["download_path"]

        result = client.torrents_add(**kwargs)
        if result == "Ok.":
            return jsonify({"status": "ok", "message": "The plunderin' has begun!"})
        return jsonify({"status": "error", "message": f"qBittorrent says: {result}"}), 400
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Failed to start plunderin'!",
            "details": traceback.format_exc(),
        }), 500


@app.route("/api/torrents/<torrent_hash>/resume", methods=["POST"])
def api_resume_torrent(torrent_hash):
    try:
        client = get_qbt_client()
        client.torrents_resume(torrent_hashes=torrent_hash)
        return jsonify({"status": "ok", "message": "Set sail!"})
    except Exception:
        return jsonify({"status": "error", "message": "Couldn't hoist the sails!"}), 500


@app.route("/api/torrents/<torrent_hash>/pause", methods=["POST"])
def api_pause_torrent(torrent_hash):
    try:
        client = get_qbt_client()
        client.torrents_pause(torrent_hashes=torrent_hash)
        return jsonify({"status": "ok", "message": "Dropped anchor!"})
    except Exception:
        return jsonify({"status": "error", "message": "Couldn't drop anchor!"}), 500


@app.route("/api/torrents/<torrent_hash>/delete", methods=["POST"])
def api_delete_torrent(torrent_hash):
    try:
        data = request.get_json() or {}
        delete_files = data.get("delete_files", False)
        client = get_qbt_client()
        client.torrents_delete(delete_files=delete_files, torrent_hashes=torrent_hash)
        return jsonify({"status": "ok", "message": "Walked the plank!"})
    except Exception:
        return jsonify({"status": "error", "message": "Couldn't make 'em walk the plank!"}), 500


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    try:
        data = request.get_json()
        config = load_config()
        config["qbittorrent"]["host"] = data.get("host", config["qbittorrent"]["host"])
        config["qbittorrent"]["port"] = int(data.get("port", config["qbittorrent"]["port"]))
        config["qbittorrent"]["username"] = data.get("username", config["qbittorrent"]["username"])
        config["qbittorrent"]["password"] = data.get("password", config["qbittorrent"]["password"])
        config["download_path"] = data.get("download_path", config.get("download_path", ""))
        save_config(config)
        return jsonify({"status": "ok", "message": "Captain's orders saved!"})
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Couldn't save the captain's orders!",
            "details": traceback.format_exc(),
        }), 500


@app.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    try:
        client = get_qbt_client()
        version = client.app.version
        api_version = client.app.web_api_version
        return jsonify({
            "status": "ok",
            "message": f"Ship is seaworthy! qBittorrent {version} (API {api_version})",
        })
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Can't reach the ship! Check yer coordinates and credentials.",
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
