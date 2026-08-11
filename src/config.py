"""
Konfigurasi terpusat untuk IdlixDownloader.

Semua konstanta domain dan URL player didefinisikan di sini.
Override via environment variable jika diperlukan.

Author  :   ibnu-sodik (fork)
Original:   sandroputraa
"""

import os
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Base URL IDLIX — dapat di-override via env var IDLIX_BASE_URL
# ---------------------------------------------------------------------------
IDLIX_BASE_URL: str = os.getenv(
    "IDLIX_BASE_URL",
    "https://z2.idlixku.com/",
)

# ---------------------------------------------------------------------------
# Daftar mirror fallback (urut prioritas).
# Mirror pertama = default jika env var tidak di-set.
# ---------------------------------------------------------------------------
IDLIX_MIRRORS: list[str] = [
    "https://z2.idlixku.com/",
    "https://tv10.idlixku.com/",
    # Tambahkan mirror baru di sini
]

# ---------------------------------------------------------------------------
# Player backend URL — dapat di-override via env var IDLIX_PLAYER_URL
# ---------------------------------------------------------------------------
PLAYER_BASE_URL: str = os.getenv(
    "IDLIX_PLAYER_URL",
    "https://jeniusplay.com",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def get_hostname(url: str) -> str:
    """Extract hostname dari URL (tanpa port)."""
    return urlparse(url).hostname or ""


def get_known_hostnames() -> set[str]:
    """Kumpulkan semua hostname dari mirror list."""
    return {get_hostname(m) for m in IDLIX_MIRRORS}