"""
YTS Scraper — Plunders the YTS catalog into media_library.json.

Usage:
    python scrape_yts.py                        # scrape all (safe defaults)
    python scrape_yts.py --pages 5              # scrape first 5 pages only (testing)
    python scrape_yts.py --delay 1.0            # 1 second between requests (default: 2.0)
    python scrape_yts.py --workers 4            # parallel threads (default: 3)
    python scrape_yts.py --resume               # resume from where you left off

Each browse page has ~20 movies. Total: ~74,000 movies across ~3,695 pages.

Rate-limiting:
    Default delay is 2.0s per request (conservative). If you're not getting
    blocked, you can lower it:  --delay 1.0 or --delay 0.5
    If you get 429/503 errors, the scraper automatically backs off.
"""

import argparse
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://en.yts-official.top"
BROWSE_URL = f"{BASE_URL}/browse-movies?page={{}}"
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_library.json")
PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scrape_progress.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 30

# Shared lock for thread-safe delay enforcement
_request_lock = threading.Lock()
_last_request_time = 0.0
_backoff_until = 0.0


def rate_limited_get(url, delay, retries=3):
    """Thread-safe, rate-limited HTTP GET with automatic backoff on 429/503."""
    global _last_request_time, _backoff_until

    for attempt in range(retries):
        with _request_lock:
            now = time.time()

            # Respect backoff if we got rate-limited
            if now < _backoff_until:
                wait = _backoff_until - now
                time.sleep(wait)
                now = time.time()

            # Enforce minimum delay between requests across all threads
            elapsed = now - _last_request_time
            if elapsed < delay:
                time.sleep(delay - elapsed + random.uniform(0, delay * 0.3))

            _last_request_time = time.time()

        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

            if resp.status_code in (429, 503):
                wait_time = min(60, (2 ** attempt) * 10 + random.uniform(0, 5))
                print(f"  ⚠️  Rate limited ({resp.status_code})! Backing off {wait_time:.0f}s...")
                with _request_lock:
                    _backoff_until = time.time() + wait_time
                time.sleep(wait_time)
                continue

            if resp.status_code == 403:
                wait_time = 30 + random.uniform(0, 15)
                print(f"  ⚠️  Forbidden (403)! Backing off {wait_time:.0f}s...")
                with _request_lock:
                    _backoff_until = time.time() + wait_time
                time.sleep(wait_time)
                continue

            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")

        except requests.exceptions.RequestException as e:
            if attempt == retries - 1:
                print(f"  ✗ Failed after {retries} attempts: {url} — {e}")
                return None
            wait_time = (2 ** attempt) * 3 + random.uniform(0, 2)
            time.sleep(wait_time)

    return None


def get_total_pages(soup):
    pagination = soup.select("ul.tsc_pagination a")
    if pagination:
        last_page = pagination[-1].get("href", "")
        match = re.search(r"page=(\d+)", last_page)
        if match:
            return int(match.group(1))
    return 1


def scrape_browse_page(page_num, delay):
    url = BROWSE_URL.format(page_num)
    soup = rate_limited_get(url, delay)
    if not soup:
        return []

    movie_urls = []
    for link in soup.select("div.browse-movie-wrap a.browse-movie-link"):
        href = link.get("href", "")
        if href and "/movies/" in href:
            full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
            if full_url not in movie_urls:
                movie_urls.append(full_url)
    return movie_urls


def scrape_movie_page(url, delay):
    soup = rate_limited_get(url, delay)
    if not soup:
        return None

    try:
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
        category = genres[0].lower() if genres else "movie"

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
                image_url = urljoin(BASE_URL, image_url)

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
                dn_match = re.search(r"dn=([^&]+)", magnet)
                if dn_match:
                    dn = dn_match.group(1).lower()
                    for t in ("2160p", "1080p", "720p"):
                        if t in dn:
                            tier = t
                            break

            if tier and quality_tiers[tier] is None:
                quality_label = label if label_lower not in ("download", "magnet", "") else tier
                quality_tiers[tier] = {"url": magnet, "quality": quality_label}

        torrents = {k: v for k, v in quality_tiers.items() if v is not None}
        if not torrents:
            return None

        slug = url.rstrip("/").split("/")[-1]

        return {
            "id": slug,
            "title": title,
            "year": year,
            "category": category,
            "genres": genres,
            "description": description,
            "torrents": torrents,
            "image_url": image_url,
            "source_url": url,
        }
    except Exception as e:
        print(f"  ✗ Error parsing {url}: {e}")
        return None


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"last_page": 0, "movie_urls": [], "scraped_urls": set()}


def save_progress(progress):
    p = {**progress, "scraped_urls": list(progress.get("scraped_urls", set()))}
    with open(PROGRESS_FILE, "w") as f:
        json.dump(p, f)


def load_existing_results():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r") as f:
            return json.load(f)
    return []


def save_results(results):
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Scrape YTS movie catalog")
    parser.add_argument("--pages", type=int, default=0, help="Max browse pages to scrape (0 = all)")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests (default: 2.0)")
    parser.add_argument("--workers", type=int, default=3, help="Parallel threads for movie pages (default: 3)")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    args = parser.parse_args()

    delay = args.delay
    max_workers = args.workers

    print("🏴‍☠️ YTS Scraper — Plunderin' the movie catalog...")
    print(f"   Delay: {delay}s between requests | Workers: {max_workers} threads")
    print()

    # --- Phase 1: Collect movie URLs from browse pages ---
    progress = load_progress() if args.resume else {"last_page": 0, "movie_urls": [], "scraped_urls": set()}
    if args.resume and isinstance(progress.get("scraped_urls"), list):
        progress["scraped_urls"] = set(progress["scraped_urls"])

    existing_results = load_existing_results() if args.resume else []
    if args.resume:
        progress["scraped_urls"] = {m["source_url"] for m in existing_results}

    print("📜 Phase 1: Collectin' movie URLs from browse pages...")
    first_page_soup = rate_limited_get(BROWSE_URL.format(1), delay)
    if not first_page_soup:
        print("✗ Couldn't reach YTS! Abortin'.")
        return

    total_pages = get_total_pages(first_page_soup)
    if args.pages > 0:
        total_pages = min(total_pages, args.pages)

    start_page = progress["last_page"] + 1 if args.resume else 1
    movie_urls = list(progress.get("movie_urls", []))

    print(f"  Found {total_pages} total pages. Starting from page {start_page}.")

    est_time_phase1 = (total_pages - start_page + 1) * delay
    print(f"  Estimated time for Phase 1: ~{est_time_phase1 / 60:.0f} minutes")
    print()

    for page_num in range(start_page, total_pages + 1):
        urls = scrape_browse_page(page_num, delay)
        movie_urls.extend(urls)

        if page_num % 10 == 0 or page_num == total_pages:
            progress["last_page"] = page_num
            progress["movie_urls"] = movie_urls
            save_progress(progress)
            print(f"  📄 Page {page_num}/{total_pages} — {len(movie_urls)} movies found so far")

    unique_urls = list(dict.fromkeys(movie_urls))
    urls_to_scrape = [u for u in unique_urls if u not in progress.get("scraped_urls", set())]

    print()
    print(f"  🗺️  Total unique movies: {len(unique_urls)}")
    print(f"  🆕  New to scrape: {len(urls_to_scrape)}")

    if urls_to_scrape:
        est_time_phase2 = len(urls_to_scrape) * delay / max_workers
        print(f"  ⏱️  Estimated time for Phase 2: ~{est_time_phase2 / 3600:.1f} hours")
    print()

    # --- Phase 2: Scrape each movie detail page ---
    print(f"⚓ Phase 2: Plunderin' movie details ({max_workers} threads, {delay}s delay)...")
    print()

    results = list(existing_results)
    scraped_count = len(results)
    failed_count = 0
    total_to_scrape = len(urls_to_scrape)
    batch_size = 25

    phase2_start = time.time()

    for batch_start in range(0, total_to_scrape, batch_size):
        batch = urls_to_scrape[batch_start:batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(scrape_movie_page, url, delay): url for url in batch}
            for future in as_completed(futures):
                movie = future.result()
                if movie:
                    results.append(movie)
                    scraped_count += 1
                else:
                    failed_count += 1

        done = batch_start + len(batch)
        elapsed = time.time() - phase2_start
        rate = done / elapsed if elapsed > 0 else 0
        remaining = (total_to_scrape - done) / rate / 60 if rate > 0 else 0
        print(f"  🍾 {done}/{total_to_scrape} — {scraped_count} scraped, {failed_count} failed | ~{remaining:.0f}m remaining")

        save_results(results)
        progress["scraped_urls"] = {m["source_url"] for m in results}
        progress["last_page"] = total_pages
        save_progress(progress)

    # Cleanup
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

    total_time = time.time() - phase2_start
    print()
    print(f"✅ Done! {len(results)} movies saved to {OUTPUT_FILE}")
    print(f"   {failed_count} movies failed/skipped")
    print(f"   Total time: {total_time / 3600:.1f} hours")


if __name__ == "__main__":
    main()
