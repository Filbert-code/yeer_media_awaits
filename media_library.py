"""
The Treasure Chest — yer hardcoded list of plunderable media.

Each entry is a dict with:
  - title:       Display name of the movie/show
  - year:        Release year
  - category:    e.g. "movie", "tv", "documentary"
  - description: Short description
  - torrent_url: Magnet link or .torrent URL
  - image_url:   (optional) Poster/thumbnail URL

Add yer own entries below, matey!
"""

MEDIA_LIBRARY = [
    # === EXAMPLE ENTRIES (replace with yer own) ===
    # {
    #     "id": "treasure-island-1950",
    #     "title": "Treasure Island",
    #     "year": 1950,
    #     "category": "movie",
    #     "description": "A young lad finds a treasure map and sets sail on a grand adventure.",
    #     "torrent_url": "magnet:?xt=urn:btih:EXAMPLE_HASH_HERE",
    #     "image_url": "",
    # },
    {
        "id": "first",
        "title": "Pirates of the Caribbean: The Curse of the Black Pearl",
        "year": 2003,
        "category": "movie",
        "description": "This swash-buckling tale follows the quest of Captain Jack Sparrow, a savvy pirate, and Will Turner, a resourceful blacksmith, as they search for Elizabeth Swann. Elizabeth, the daughter of the governor and the love of Will's life, has been kidnapped by the feared Captain Barbossa. Little do they know, but the fierce and clever Barbossa has been cursed. He, along with his large crew, are under an ancient curse, doomed for eternity to neither live, nor die. That is, unless a blood sacrifice is made. —the lexster",
        "torrent_url": "https://en.yts-official.top/torrent/Pirates%20of%20the%20Caribbean%20The%20Curse%20of%20the%20Black%20Pearl%20(2003)%20720p.BluRay.torrent",
        "image_url": "https://en.yts-official.top/movies/poster/pirates-of-the-caribbean-the-curse-of-the-black-pearl-2003.jpg",
    },
]
