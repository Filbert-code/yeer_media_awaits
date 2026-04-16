import json
import os
import re
import traceback
from urllib.parse import urljoin, quote_plus

from flask import Flask, jsonify, redirect, render_template, request, url_for
import qbittorrentapi
import requests
from bs4 import BeautifulSoup

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


# ─── YTS Live Search ──────────────────────────────────────────────────

YTS_BASE = "https://en.yts-official.top"
YTS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def yts_fetch(url):
    resp = requests.get(url, headers=YTS_HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def extract_quality_from_magnet(magnet_url):
    """Extract quality tier (720p, 1080p, 2160p) from a magnet URL's dn= param."""
    dn_match = re.search(r"dn=([^&]+)", magnet_url)
    if dn_match:
        dn = dn_match.group(1).lower()
        for tier in ("2160p", "1080p", "720p"):
            if tier in dn:
                return tier
    return None


def parse_magnet_torrents(soup):
    """Parse all magnet links from a movie page into quality-keyed dict."""
    quality_tiers = {"720p": None, "1080p": None, "2160p": None}
    seen_hashes = set()
    for a in soup.select("a[href^='magnet:']"):
        magnet = a.get("href", "")
        if not magnet:
            continue
        hash_match = re.search(r"btih:([A-Fa-f0-9]+)", magnet)
        if not hash_match:
            continue
        mag_hash = hash_match.group(1).upper()
        if mag_hash in seen_hashes:
            continue
        seen_hashes.add(mag_hash)

        label = a.get_text(strip=True)
        label_lower = label.lower()

        tier = None
        for t in ("2160p", "1080p", "720p"):
            if t in label_lower:
                tier = t
                break

        if not tier:
            tier = extract_quality_from_magnet(magnet)

        if tier and quality_tiers[tier] is None:
            quality_label = label if label.lower() not in ("download", "magnet", "") else f"{tier}"
            quality_tiers[tier] = {"url": magnet, "quality": quality_label}

    return {k: v for k, v in quality_tiers.items() if v is not None}


def yts_search(keyword, page=1):
    url = f"{YTS_BASE}/browse-movies?keyword={quote_plus(keyword)}&page={page}"
    soup = yts_fetch(url)

    results = []
    for card in soup.select("div.browse-movie-wrap"):
        link = card.select_one("a.browse-movie-link")
        if not link:
            continue
        href = link.get("href", "")
        if not href or "/movies/" not in href:
            continue
        movie_url = href if href.startswith("http") else urljoin(YTS_BASE, href)

        title_el = card.select_one("a.browse-movie-title")
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        year_el = card.select_one("div.browse-movie-year")
        year = year_el.get_text(strip=True) if year_el else ""

        img_el = card.select_one("img")
        image_url = ""
        if img_el:
            image_url = img_el.get("data-src") or img_el.get("src") or ""
            if image_url and not image_url.startswith("http"):
                image_url = urljoin(YTS_BASE, image_url)

        rating_el = card.select_one("h4.rating")
        rating = rating_el.get_text(strip=True) if rating_el else ""

        results.append({
            "url": movie_url,
            "title": title,
            "year": year,
            "image_url": image_url,
            "rating": rating,
        })

    total_el = soup.select_one("h2.browse-movie-count, div.browse-content h2")
    total_text = total_el.get_text(strip=True) if total_el else ""
    total_match = re.search(r"([\d,]+)", total_text)
    total_count = int(total_match.group(1).replace(",", "")) if total_match else len(results)

    return {"results": results, "total": total_count, "page": page}


def yts_movie_detail(movie_url):
    soup = yts_fetch(movie_url)

    title_el = soup.select_one("#movie-info h1")
    title = title_el.get_text(strip=True) if title_el else "Unknown"

    year = 0
    genres = []
    for h2 in soup.select("#movie-info h2"):
        text = h2.get_text(strip=True)
        year_match = re.search(r"^(\d{4})$", text)
        if year_match:
            year = int(year_match.group(1))
        elif "/" in text:
            genres = [g.strip() for g in text.split("/") if g.strip()]

    synopsis_el = soup.select_one("#synopsis p, .synopsis p, #movie-info p.movie-description-full")
    if not synopsis_el:
        for p in soup.select("#movie-info p"):
            text = p.get_text(strip=True)
            if len(text) > 40:
                synopsis_el = p
                break
    description = synopsis_el.get_text(strip=True) if synopsis_el else ""

    img_el = soup.select_one("#movie-poster img, img.img-responsive[data-src]")
    if not img_el:
        img_el = soup.select_one("div#movie-poster img, img.img-responsive")
    image_url = ""
    if img_el:
        image_url = img_el.get("data-src") or img_el.get("src") or ""
        if image_url and not image_url.startswith("http"):
            image_url = urljoin(YTS_BASE, image_url)

    torrents = parse_magnet_torrents(soup)

    return {
        "title": title,
        "year": year,
        "genres": genres,
        "description": description,
        "image_url": image_url,
        "torrents": torrents,
    }


@app.route("/search")
def search_page():
    return render_template("search.html")


@app.route("/direct")
def direct_plunder_page():
    return render_template("direct_plunder.html")


@app.route("/api/yts/search")
def api_yts_search():
    keyword = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    if not keyword:
        return jsonify({"status": "error", "message": "No search term provided!"}), 400
    try:
        data = yts_search(keyword, page)
        return jsonify({"status": "ok", **data})
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Search failed! YTS might be down.",
            "details": traceback.format_exc(),
        }), 500


@app.route("/api/yts/movie")
def api_yts_movie():
    movie_url = request.args.get("url", "").strip()
    if not movie_url:
        return jsonify({"status": "error", "message": "No movie URL provided!"}), 400
    try:
        data = yts_movie_detail(movie_url)
        return jsonify({"status": "ok", **data})
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Couldn't fetch movie details!",
            "details": traceback.format_exc(),
        }), 500


# ─── 1337x + IMDB TV Show Search ─────────────────────────────────────

L337X_BASE = "https://1337x.pro"
IMDB_SUGGEST_URL = "https://v2.sg.media-imdb.com/suggestion/{letter}/{query}.json"
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def imdb_search(query):
    """Search IMDB suggestions API for TV shows."""
    letter = query[0].lower() if query else "a"
    url = IMDB_SUGGEST_URL.format(letter=letter, query=quote_plus(query))
    resp = requests.get(url, headers=COMMON_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in data.get("d", []):
        qtype = item.get("q", "")
        if "TV" not in qtype and "series" not in qtype.lower():
            continue
        results.append({
            "imdb_id": item.get("id", ""),
            "title": item.get("l", "Unknown"),
            "year": item.get("y", ""),
            "type": qtype,
            "image_url": item.get("i", {}).get("imageUrl", "") if isinstance(item.get("i"), dict) else "",
        })
    return results


def l337x_fetch(url):
    """Fetch and parse an HTML page from 1337x."""
    resp = requests.get(url, headers=COMMON_HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _parse_season_episode(title):
    """Extract season and episode numbers from a torrent title."""
    se_match = re.search(r"S(\d{1,2})\s*E(\d{1,3})", title, re.IGNORECASE)
    if se_match:
        return int(se_match.group(1)), int(se_match.group(2))
    s_match = re.search(r"S(\d{1,2})\b", title, re.IGNORECASE)
    if s_match:
        return int(s_match.group(1)), 0
    season_match = re.search(r"Season\s*(\d{1,2})", title, re.IGNORECASE)
    if season_match:
        ep_match = re.search(r"Episode\s*(\d{1,3})", title, re.IGNORECASE)
        return int(season_match.group(1)), int(ep_match.group(1)) if ep_match else 0
    return 0, 0


def _parse_quality(title):
    """Extract quality tier from a torrent title."""
    title_lower = title.lower()
    if "2160p" in title_lower or "4k" in title_lower:
        return "2160p"
    if "1080p" in title_lower:
        return "1080p"
    if "720p" in title_lower:
        return "720p"
    if "480p" in title_lower:
        return "480p"
    return "SD"


def _is_season_pack(title, season, episode):
    """Determine if a torrent is a season pack."""
    title_lower = title.lower()
    if episode == 0 and season > 0:
        return True
    if (re.search(r"S\d{1,2}\b", title, re.IGNORECASE)
            and not re.search(r"S\d{1,2}\s*E\d", title, re.IGNORECASE)):
        return True
    if "complete" in title_lower:
        return True
    if re.search(r"season\s*\d+", title_lower) and "episode" not in title_lower:
        return True
    return False


def l337x_search_page(query, page=1):
    """Scrape a single page of 1337x search results."""
    url = f"{L337X_BASE}/search/?q={quote_plus(query)}&page={page}"
    soup = l337x_fetch(url)

    results = []
    for tr in soup.select("table tbody tr"):
        cells = tr.select("td")
        if len(cells) < 3:
            continue

        name_cell = cells[0]
        link = None
        for a in name_cell.select("a"):
            href = a.get("href", "")
            if "/torrent/" in href and a.get_text(strip=True):
                link = a
                break
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link.get("href", "")
        torrent_url = href if href.startswith("http") else urljoin(L337X_BASE, href)

        try:
            seeds = int(cells[1].get_text(strip=True))
        except (ValueError, IndexError):
            seeds = 0
        try:
            leechers = int(cells[2].get_text(strip=True))
        except (ValueError, IndexError):
            leechers = 0

        size = "?"
        for cell in cells[3:]:
            size_match = re.search(
                r"([\d.]+\s*(?:KB|MB|GB|TB))", cell.get_text(strip=True), re.IGNORECASE
            )
            if size_match:
                size = size_match.group(1)
                break

        results.append({
            "title": title,
            "torrent_url": torrent_url,
            "seeds": seeds,
            "leechers": leechers,
            "size": size,
        })

    has_next = bool(soup.find("a", string=re.compile(r"Next", re.IGNORECASE)))

    return {"results": results, "has_next": has_next, "page": page}


def l337x_search_all(query):
    """Fetch ALL search results from 1337x, auto-paginating with delays."""
    import time as _time

    all_results = []
    page = 1

    while True:
        data = l337x_search_page(query, page)
        page_results = data["results"]

        if not page_results:
            break

        all_results.extend(page_results)

        if not data["has_next"]:
            break

        page += 1
        _time.sleep(1.5)

    torrents = []
    for t in all_results:
        season, episode = _parse_season_episode(t["title"])
        quality = _parse_quality(t["title"])
        title = t["title"]

        torrents.append({
            "title": title,
            "season": season,
            "episode": episode,
            "quality": quality,
            "size": t["size"],
            "seeds": t["seeds"],
            "peers": t["leechers"],
            "torrent_url": t["torrent_url"],
            "is_season_pack": _is_season_pack(title, season, episode),
        })

    torrents.sort(key=lambda x: (x["season"], x["episode"], -x["seeds"]), reverse=True)

    return {
        "total": len(torrents),
        "torrents": torrents,
    }


def l337x_get_magnet(torrent_url):
    """Fetch a torrent's detail page and construct a magnet link from info hash."""
    soup = l337x_fetch(torrent_url)

    info_hash = None
    page_text = soup.get_text()
    hash_match = re.search(r"Info\s*Hash\s*:?\s*([A-Fa-f0-9]{40})", page_text)
    if hash_match:
        info_hash = hash_match.group(1).upper()

    if not info_hash:
        return None

    trackers = []
    for li in soup.select("li"):
        text = li.get_text(strip=True)
        if text.startswith(("udp://", "http://", "https://")) and "announce" in text:
            trackers.append(text)

    title_el = soup.select_one("h1")
    title = title_el.get_text(strip=True) if title_el else ""

    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    if title:
        magnet += f"&dn={quote_plus(title)}"
    for tracker in trackers:
        magnet += f"&tr={quote_plus(tracker)}"

    return magnet


@app.route("/api/tv/search")
def api_tv_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"status": "error", "message": "No search term provided!"}), 400
    try:
        results = imdb_search(query)
        return jsonify({"status": "ok", "results": results})
    except Exception:
        return jsonify({
            "status": "error",
            "message": "IMDB search failed!",
            "details": traceback.format_exc(),
        }), 500


@app.route("/api/tv/torrents")
def api_tv_torrents():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"status": "error", "message": "No search term provided!"}), 400
    try:
        data = l337x_search_all(query)
        return jsonify({"status": "ok", **data})
    except Exception:
        return jsonify({
            "status": "error",
            "message": "1337x search failed!",
            "details": traceback.format_exc(),
        }), 500


@app.route("/api/tv/magnet")
def api_tv_magnet():
    torrent_url = request.args.get("url", "").strip()
    if not torrent_url:
        return jsonify({"status": "error", "message": "No torrent URL provided!"}), 400
    try:
        magnet = l337x_get_magnet(torrent_url)
        if magnet:
            return jsonify({"status": "ok", "magnet_url": magnet})
        return jsonify({
            "status": "error",
            "message": "Could not extract magnet link from page!",
        }), 404
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Failed to fetch torrent details!",
            "details": traceback.format_exc(),
        }), 500


# ─── Direct Plunder (paste URL / magnet) ─────────────────────────────

def resolve_torrent_url(url):
    """Given a URL or magnet link, resolve it to downloadable torrent info."""
    url = url.strip()

    if url.startswith("magnet:"):
        return {"type": "magnet", "magnet_url": url}

    if "1337x" in url and "/torrent/" in url:
        magnet = l337x_get_magnet(url)
        if magnet:
            return {"type": "magnet", "magnet_url": magnet, "source": "1337x"}
        return None

    if "yts" in url and "/movies/" in url:
        soup = yts_fetch(url)
        torrents = parse_magnet_torrents(soup)
        title_el = soup.select_one("#movie-info h1")
        title = title_el.get_text(strip=True) if title_el else "Unknown"
        if torrents:
            return {"type": "yts", "title": title, "torrents": torrents}
        return None

    # Generic fallback: try to find magnet links or info hash on any page
    try:
        resp = requests.get(url, headers=COMMON_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        magnet_link = soup.select_one("a[href^='magnet:']")
        if magnet_link:
            return {"type": "magnet", "magnet_url": magnet_link["href"], "source": "generic"}

        page_text = soup.get_text()
        hash_match = re.search(r"Info\s*Hash\s*:?\s*([A-Fa-f0-9]{40})", page_text)
        if hash_match:
            info_hash = hash_match.group(1).upper()
            return {"type": "magnet", "magnet_url": f"magnet:?xt=urn:btih:{info_hash}", "source": "generic"}
    except Exception:
        pass

    return None


@app.route("/api/direct-plunder", methods=["POST"])
def api_direct_plunder():
    data = request.get_json()
    url = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"status": "error", "message": "No URL or magnet link provided!"}), 400
    try:
        result = resolve_torrent_url(url)
        if not result:
            return jsonify({
                "status": "error",
                "message": "Couldn't find any treasure at that URL! Make sure it's a valid torrent page or magnet link.",
            }), 404
        return jsonify({"status": "ok", **result})
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Failed to process that URL!",
            "details": traceback.format_exc(),
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
