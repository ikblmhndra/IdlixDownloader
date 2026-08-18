"""
Helper Class for IDLIX Downloader & IDLIX Player CLI

Update  :   2026-08-11
Author  :   sandroputraa
Refactor:   ibnu-sodik (API-based flow, config terpusat, multi-mirror)

Flow baru (API-based, tanpa Selenium/Playwright):
  1. get_video_data(url)  → parse slug → GET /api/movies/{slug} → UUID + title
  2. get_embed_url()      → play-info → wait gate → claim → redeem → master M3U8 URL
  3. get_m3u8_url()       → parse master M3U8 → variant playlist (resolusi)
  4. download_m3u8()      → ffmpeg mux video+audio → .mp4
  5. play_m3u8()          → ffplay master URL
"""

import os
import re
import json
import time
import random
import shutil
import zipfile
import subprocess
from typing import Any

import requests
from loguru import logger
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from vtt_to_srt.vtt_to_srt import ConvertFile
from curl_cffi import requests as cffi_requests

from src import config

class IdlixHelper:
    # ------------------------------------------------------------------
    # Static headers template
    # ------------------------------------------------------------------
    BASE_STATIC_HEADERS = {
        "Connection": "keep-alive",
        "sec-ch-ua": "Not)A;Brand;v=99, Google Chrome;v=127, Chromium;v=127",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "Windows",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    }

    def __init__(self) -> None:
        self.poster: str | None = None
        self.m3u8_url: str | None = None
        self.video_id: str | None = None       # UUID dari API
        self.video_uuid: str | None = None      # alias
        self.embed_url: str | None = None       # master M3U8 URL (kompatibilitas)
        self.video_name: str | None = None
        self.video_slug: str | None = None
        self.content_type: str = "movie"   # "movie" | "episode"
        self.is_subtitle: bool | None = None
        self.variant_playlist = None

        # Audio playlist URL (terpisah dari video di HLS baru)
        self._audio_playlist_url: str | None = None

        # Subtitle tracks dari redeem response
        # Format: [{"lang": "id", "label": "Indonesian", "path": "https://...vtt"}, ...]
        self._subtitle_tracks: list[dict[str, str]] = []

        # Master M3U8 URL (raw text)
        self._master_m3u8_url: str | None = None
        self._master_m3u8_text: str | None = None

        # Base URL — diambil dari config
        self._base_url: str = config.IDLIX_BASE_URL

        # Build headers — NOTE: "Host" TIDAK di-set manual,
        # karena curl_cffi akan set otomatis per-request.
        self._headers = {
            **self.BASE_STATIC_HEADERS,
            "Referer": self._base_url,
            "Origin": self._base_url.rstrip("/"),
        }

        self.request = cffi_requests.Session(
            impersonate=random.choice(["chrome124", "chrome119", "chrome104"]),
            headers=self._headers,
            debug=False,
        )

        # FFMPEG check
        self._ensure_ffmpeg()

    # ------------------------------------------------------------------
    # Property: base_url
    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, url: str) -> None:
        self._base_url = url
        self._headers = {
            **self.BASE_STATIC_HEADERS,
            "Referer": url,
            "Origin": url.rstrip("/"),
        }
        self.request.headers.update(self._headers)
        logger.info(f"Base URL switched to: {url}")

    # ------------------------------------------------------------------
    # FFMPEG setup
    # ------------------------------------------------------------------
    def _ensure_ffmpeg(self) -> None:
        if os.name == "nt":
            for p in os.environ.get("path", "").split(";"):
                if "ffmpeg" in p:
                    logger.info(f"FFMPEG Found: {p}")
                    break
            else:
                if not os.path.exists("ffmpeg-release-essentials.zip"):
                    self.download_ffmpeg()
                logger.warning("FFMPEG not set in PATH, Trying set PATH")
                try:
                    with zipfile.ZipFile("ffmpeg-release-essentials.zip", "r") as zf:
                        zf.extractall(
                            os.path.join(
                                os.path.dirname(os.path.abspath(__file__)), "ffmpeg"
                            )
                        )
                    logger.success("Success Extracting ffmpeg")
                    path = ""
                    ffmpeg_dir = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "ffmpeg"
                    )
                    for entry in os.listdir(ffmpeg_dir):
                        if "ffmpeg" in entry:
                            path = os.path.join(ffmpeg_dir, entry, "bin")
                            logger.info(f"Found: {path}")
                            break
                    else:
                        logger.error(
                            "FFMPEG not found, please install ffmpeg first"
                        )
                    subprocess.call(["setx", "PATH", "%PATH%;" + path])
                    logger.success(
                        "FFMPEG PATH set successfully, Please restart the program"
                    )
                    exit()
                except Exception as e:
                    print(f"Error: {e}")
        else:
            if not shutil.which("ffmpeg"):
                logger.error(
                    "FFMPEG not found, please install ffmpeg first before running this script"
                )
                exit()

    @staticmethod
    def download_ffmpeg() -> None:
        try:
            logger.info("Downloading ffmpeg")
            content = requests.get(
                url="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
                stream=True,
            )
            with open("ffmpeg-release-essentials.zip", mode="wb") as f:
                for chunk in content.iter_content(chunk_size=1024):
                    print(
                        "\rDownloading: {} MB of {} MB".format(
                            round(
                                os.path.getsize("ffmpeg-release-essentials.zip")
                                / 1024
                                / 1024,
                                2,
                            ),
                            round(
                                int(content.headers.get("Content-Length", 0))
                                / 1024
                                / 1024,
                                2,
                            ),
                        ),
                        end="",
                    )
                    f.write(chunk)
            print()
            logger.success("Downloaded ffmpeg")
        except Exception as e:
            print(f"Error: {e}")

    # ------------------------------------------------------------------
    # Homepage (tetap kompatibel — scraping HTML)
    # ------------------------------------------------------------------
    def get_home(self) -> dict[str, Any]:
        try:
            request = self.request.get(url=self.base_url, timeout=10)
            if request.status_code != 200:
                return {
                    "status": False,
                    "message": f"Failed to get home page, status: {request.status_code}",
                }

            bs = BeautifulSoup(request.text, "html.parser")
            tmp_featured: list[dict[str, Any]] = []

            # --- Strategy 1: Parse JSON-LD for featured items ---
            json_ld_scripts = bs.find_all("script", type="application/ld+json")
            featured_list = None
            for script in json_ld_scripts:
                try:
                    data = json.loads(script.string)
                    if data.get("@type") == "ItemList":
                        featured_list = data.get("itemListElement", [])
                        break
                except (json.JSONDecodeError, AttributeError):
                    continue

            if not featured_list:
                # --- Fallback: Parse <a> tags ---
                logger.warning(
                    "Could not find JSON-LD ItemList, falling back to <a> tags."
                )
                all_links = bs.find_all("a", href=True)
                movie_links = [
                    a
                    for a in all_links
                    if a["href"].startswith(("/movie/", "/series/"))
                ]
                seen_urls: set[str] = set()
                unique_links = []
                for a in movie_links:
                    if a["href"] not in seen_urls:
                        seen_urls.add(a["href"])
                        unique_links.append(a)

                for link in unique_links:
                    url = link["href"]
                    parts = [p for p in url.split("/") if p]
                    if len(parts) < 2:
                        continue
                    item_type = parts[0]
                    slug = parts[1]
                    slug_parts = slug.rsplit("-", 1)
                    title = slug_parts[0].replace("-", " ").title()
                    year = (
                        slug_parts[1]
                        if len(slug_parts) > 1 and slug_parts[1].isdigit()
                        else "N/A"
                    )
                    img = link.find("img")
                    poster = img["src"] if img else None
                    if poster and poster.startswith("//"):
                        poster = "https:" + poster
                    tmp_featured.append(
                        {
                            "url": self.base_url.strip("/") + url,
                            "title": title,
                            "year": year,
                            "type": item_type,
                            "poster": poster,
                        }
                    )
            else:
                # --- Primary: JSON-LD data ---
                for item in featured_list:
                    if item.get("@type") != "ListItem":
                        continue
                    url = urlparse(item.get("url", "")).path
                    if not url or not (
                        url.startswith("/movie/") or url.startswith("/series/")
                    ):
                        continue
                    parts = [p for p in url.split("/") if p]
                    item_type = parts[0]
                    slug = parts[1]
                    slug_parts = slug.rsplit("-", 1)
                    title = slug_parts[0].replace("-", " ").title()
                    year = (
                        slug_parts[1]
                        if len(slug_parts) > 1 and slug_parts[1].isdigit()
                        else "N/A"
                    )
                    link_el = bs.find("a", href=url)
                    poster = None
                    if link_el:
                        img = link_el.find("img")
                        if img and "src" in img.attrs:
                            poster_path = img["src"]
                            if poster_path.startswith("//"):
                                poster = "https:" + poster_path
                            elif poster_path.startswith("/"):
                                poster = self.base_url.strip("/") + poster_path
                            else:
                                poster = poster_path
                    tmp_featured.append(
                        {
                            "url": self.base_url.strip("/") + url,
                            "title": title,
                            "year": year,
                            "type": item_type,
                            "poster": poster,
                        }
                    )

            return {"status": True, "featured_movie": tmp_featured}

        except Exception as error_get_home:
            logger.error(f"Error in get_home: {error_get_home}")
            return {"status": False, "message": str(error_get_home)}

    # ------------------------------------------------------------------
    # Video data  (NEW: API-based)
    # ------------------------------------------------------------------
    def get_video_data(self, url: str) -> dict[str, Any]:
        """
        Parse slug dari URL movie, lalu hit ``/api/movies/{slug}`` untuk
        mendapatkan UUID dan metadata.
        """
        if not url:
            return {"status": False, "message": "URL is required"}

        # Validasi hostname
        url_hostname = config.get_hostname(url)
        known = config.get_known_hostnames()
        if url_hostname not in known:
            return {
                "status": False,
                "message": (
                    f'Invalid URL — hostname "{url_hostname}" '
                    f"not in known mirrors: {known}"
                ),
            }

        # Extract slug dari path: /movie/venom-the-last-dance-2024
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            return {
                "status": False,
                "message": f"Cannot parse slug from URL path: {parsed.path}",
            }

        content_type = path_parts[0]  # "movie" or "series"
        slug = path_parts[1]
        self.video_slug = slug

        # Deteksi URL episode: /series/{slug}/season/{s}/episode/{e}
        is_episode = False
        season = episode = None
        if content_type == "series" and len(path_parts) >= 6:
            try:
                season = int(path_parts[3])
                episode = int(path_parts[5])
                is_episode = True
            except (IndexError, ValueError):
                pass

        # Hit API
        base = self.base_url.rstrip("/")
        if is_episode:
            api_url = f"{base}/api/series/{slug}/season/{season}/episode/{episode}"
            self.content_type = "episode"
        else:
            api_url = f"{base}/api/movies/{slug}"
            self.content_type = "movie"

        try:
            resp = self.request.get(
                url=api_url,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return {
                    "status": False,
                    "message": f"API {api_url.replace(base, '')} returned {resp.status_code}",
                }

            data = resp.json()
            if is_episode:
                episode_data = data.get("episode", {})
                self.video_uuid = episode_data.get("id")
                series_title = data.get("series", {}).get("title", slug.replace("-", " ").title())
                ep_title = episode_data.get("title") or ""
                self.video_name = (
                    f"{series_title} S{season}E{episode}"
                    + (f" - {ep_title}" if ep_title else "")
                )
            else:
                self.video_uuid = data.get("id")
                self.video_name = data.get("title", slug.replace("-", " ").title())

            self.video_id = self.video_uuid          # backward compat

            poster_path = data.get("posterPath")
            if not poster_path and is_episode:
                poster_path = data.get("series", {}).get("posterPath")
            if poster_path:
                self.poster = f"https://image.tmdb.org/t/p/w500{poster_path}"
            else:
                self.poster = None

            if not self.video_uuid:
                return {
                    "status": False,
                    "message": "UUID not found in API response",
                }

            return {
                "status": True,
                "video_id": self.video_id,
                "video_name": self.video_name,
                "poster": self.poster,
            }

        except Exception as e:
            return {"status": False, "message": str(e)}

    # ------------------------------------------------------------------
    # Embed URL  (NEW: play-info → wait → claim → redeem → master M3U8)
    # ------------------------------------------------------------------
    def get_embed_url(self) -> dict[str, Any]:
        """
        Mendapatkan master M3U8 URL melalui flow:
          1. GET /api/watch/play-info/movie/{uuid} → gateToken + unlockAt
          2. sleep sampai unlockAt
          3. POST /api/watch/session/claim → claim + redeemUrl
          4. POST redeemUrl → url (master M3U8)

        Return ``embed_url`` dipertahankan untuk backward-compat dengan main.py.
        """
        if not self.video_uuid:
            return {"status": False, "message": "Video UUID is required (call get_video_data first)"}

        base = self.base_url.rstrip("/")

        try:
            # Step 1: play-info
            play_info_url = f"{base}/api/watch/play-info/{self.content_type}/{self.video_uuid}"
            resp = self.request.get(
                url=play_info_url,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return {
                    "status": False,
                    "message": f"play-info returned {resp.status_code}",
                }

            pi = resp.json()
            gate_token = pi.get("gateToken")
            unlock_at = pi.get("unlockAt")
            server_now = pi.get("serverNow")

            if not gate_token:
                return {"status": False, "message": "gateToken not found in play-info"}

            # Step 2: wait for gate
            if unlock_at and server_now:
                wait_secs = (unlock_at - server_now) / 1000.0
                if wait_secs > 0:
                    logger.info(f"Waiting {wait_secs:.1f}s for gate unlock...")
                    time.sleep(wait_secs + 1)

            # Step 3: claim
            claim_url = f"{base}/api/watch/session/claim"
            resp_claim = self.request.post(
                url=claim_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={"gateToken": gate_token},
            )
            if resp_claim.status_code != 200:
                return {
                    "status": False,
                    "message": f"claim returned {resp_claim.status_code}: {resp_claim.text[:200]}",
                }

            claim_data = resp_claim.json()
            redeem_url = claim_data.get("redeemUrl")
            claim_token = claim_data.get("claim")

            if not redeem_url or not claim_token:
                return {
                    "status": False,
                    "message": f"claim response missing redeemUrl/claim: {claim_data}",
                }

            # Step 4: redeem → master M3U8 URL
            resp_redeem = self.request.post(
                url=redeem_url,
                headers={"Content-Type": "application/json"},
                json={"claim": claim_token},
            )
            if resp_redeem.status_code != 200:
                return {
                    "status": False,
                    "message": f"redeem returned {resp_redeem.status_code}: {resp_redeem.text[:200]}",
                }

            redeem_data = resp_redeem.json()
            master_url = redeem_data.get("url")
            if not master_url:
                return {
                    "status": False,
                    "message": f"redeem response missing url: {redeem_data}",
                }

            self._master_m3u8_url = master_url
            self.embed_url = master_url  # backward compat
            self._subtitle_tracks = redeem_data.get("subtitles", [])

            logger.success(f"Got master M3U8 URL: {master_url[:80]}...")
            return {"status": True, "embed_url": self.embed_url}

        except Exception as e:
            return {"status": False, "message": str(e)}

    # ------------------------------------------------------------------
    # M3U8 URL  (NEW: parse master playlist untuk variant selection)
    # ------------------------------------------------------------------
    def get_m3u8_url(self) -> dict[str, Any]:
        """
        Fetch master M3U8 playlist dan parse menjadi variant list.
        Audio track terpisah disimpan di ``self._audio_playlist_url``.
        """
        if not self._master_m3u8_url:
            return {"status": False, "message": "Master M3U8 URL not available (call get_embed_url first)"}

        try:
            resp = self.request.get(self._master_m3u8_url)
            if resp.status_code != 200:
                return {
                    "status": False,
                    "message": f"Failed to fetch master playlist: {resp.status_code}",
                }

            master_text = resp.text
            self._master_m3u8_text = master_text

            # Parse audio URI dari EXT-X-MEDIA
            audio_match = re.search(r'TYPE=AUDIO[^"]*URI="([^"]+)"', master_text)
            if audio_match:
                self._audio_playlist_url = urljoin(self._master_m3u8_url, audio_match.group(1))
                if self._audio_playlist_url:
                    logger.info(f"Audio track found: {self._audio_playlist_url[:80]}...")

            # Parse video variants dari EXT-X-STREAM-INF
            tmp_variant_playlist: list[dict[str, Any]] = []
            lines = master_text.splitlines()
            idx = 0
            vid_id = 0

            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    # Parse attributes
                    bw_m = re.search(r"BANDWIDTH=(\d+)", line)
                    res_m = re.search(r"RESOLUTION=(\d+x\d+)", line)
                    bandwidth = int(bw_m.group(1)) if bw_m else 0
                    resolution = res_m.group(1) if res_m else "N/A"

                    # Next non-empty non-comment line = URI
                    uri_line = ""
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip() and not lines[j].startswith("#"):
                            uri_line = lines[j].strip()
                            break

                    if uri_line:
                        # Resolve relative URI against master playlist URL
                        full_uri = urljoin(self._master_m3u8_url, uri_line)

                        tmp_variant_playlist.append(
                            {
                                "bandwidth": bandwidth,
                                "resolution": resolution,
                                "uri": full_uri,
                                "id": str(vid_id),
                            }
                        )
                        vid_id += 1

            if not tmp_variant_playlist:
                return {
                    "status": False,
                    "message": "No video variants found in master playlist",
                }

            # Sort by bandwidth descending (highest first)
            tmp_variant_playlist.sort(key=lambda v: v["bandwidth"], reverse=True)
            # Re-assign IDs after sort
            for i, v in enumerate(tmp_variant_playlist):
                v["id"] = str(i)

            # Set default m3u8_url ke highest quality
            self.m3u8_url = tmp_variant_playlist[0]["uri"]

            is_variant = len(tmp_variant_playlist) > 1
            return {
                "status": True,
                "m3u8_url": self.m3u8_url,
                "variant_playlist": tmp_variant_playlist,
                "is_variant_playlist": is_variant,
            }

        except Exception as e:
            return {"status": False, "message": str(e)}

    # ------------------------------------------------------------------
    # Set M3U8 URL (variant selection — dipanggil dari main.py)
    # ------------------------------------------------------------------
    def set_m3u8_url(self, m3u8_url: str) -> None:
        """Set selected variant URL."""
        self.m3u8_url = m3u8_url

    # ------------------------------------------------------------------
    # Download M3U8  (NEW: ffmpeg-based, mux video+audio)
    # ------------------------------------------------------------------
    def download_m3u8(self, output_dir: str | None = None) -> dict[str, Any]:
        """
        Download video+audio menggunakan ffmpeg.
        Karena HLS baru memisahkan video & audio track, kita perlu
        mux keduanya ke satu file MP4.
        """
        try:
            if not self.m3u8_url:
                return {"status": False, "message": "M3U8 URL is required"}
            if not self.video_name:
                return {"status": False, "message": "Video name is not set"}

            # Sanitize filename
            safe_name = re.sub(r'[<>:"/\\|?*]', "", self.video_name).strip()
            out_dir = output_dir or os.getcwd()
            output_path = os.path.join(out_dir, f"{safe_name}.mp4")

            # Build ffmpeg command
            cmd = ["ffmpeg", "-y"]

            # Input 1: video
            cmd.extend(["-i", self.m3u8_url])

            # Input 2: audio (jika ada)
            if self._audio_playlist_url:
                cmd.extend(["-i", self._audio_playlist_url])

            # Mapping & codec
            if self._audio_playlist_url:
                cmd.extend([
                    "-map", "0:v:0",     # video dari input 0
                    "-map", "1:a:0",     # audio dari input 1
                ])
            else:
                cmd.extend(["-map", "0"])

            cmd.extend([
                "-c", "copy",            # no re-encoding
                "-movflags", "+faststart",
                "-loglevel", "info",
                "-stats",
                "--", output_path,
            ])

            logger.info(f"Starting download: {safe_name}")
            logger.debug(f"ffmpeg command: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=False)

            if result.returncode != 0:
                return {
                    "status": False,
                    "message": f"ffmpeg exited with code {result.returncode}",
                }

            return {
                "status": True,
                "message": "Download success",
                "path": output_path,
            }

        except Exception as e:
            return {"status": False, "message": str(e)}

    # ------------------------------------------------------------------
    # Subtitle
    # ------------------------------------------------------------------
    def get_subtitle(self, output_dir: str | None = None) -> dict[str, Any]:
        """
        Download subtitle dari track list yang didapat via API.
        Default: pilih "Indonesian". Jika tidak ada, ambil yang pertama.
        """
        self.is_subtitle = False
        if not self.video_name:
            return {"status": False, "message": "Video name not set"}
        if not self._subtitle_tracks:
            return {"status": False, "message": "No subtitle tracks found in API response"}

        # --- Pilih track ---
        # Prioritaskan Indonesian
        sub_track = next((s for s in self._subtitle_tracks if s.get("lang") == "id"), None)
        # Fallback ke track pertama jika tidak ada
        if not sub_track:
            sub_track = self._subtitle_tracks[0]

        sub_url = sub_track.get("path")
        if not sub_url:
            return {"status": False, "message": f"Track '{sub_track.get('label')}' has no URL"}

        logger.info(f"Downloading subtitle: {sub_track.get('label')} ({sub_track.get('lang')})")

        # --- Download & Konversi VTT -> SRT ---
        try:
            # Sanitize filename
            safe_name = re.sub(r'[<>:"/\\|?*]', "", self.video_name).strip()
            target_dir = output_dir or os.getcwd()
            vtt_path = os.path.join(target_dir, f"{safe_name}.vtt")
            srt_path = os.path.join(target_dir, f"{safe_name}.srt")

            # Download VTT
            resp = self.request.get(sub_url)
            if resp.status_code != 200:
                return {
                    "status": False,
                    "message": f"Failed to download VTT: status {resp.status_code}",
                }
            with open(vtt_path, "wb") as f:
                f.write(resp.content)

            # Konversi
            self.convert_vtt_to_srt(vtt_path)
            if not os.path.exists(srt_path):
                 return {"status": False, "message": "SRT conversion failed"}

            self.is_subtitle = True
            logger.success(f"Subtitle saved: {srt_path}")
            return {
                "status": True,
                "message": "Subtitle downloaded and converted",
                "subtitle": srt_path,
            }

        except Exception as e:
            return {"status": False, "message": str(e)}

    # ------------------------------------------------------------------
    # Play M3U8  (ffplay dengan master URL)
    # ------------------------------------------------------------------
    def play_m3u8(self) -> dict[str, Any]:
        try:
            if not self.m3u8_url:
                return {"status": False, "message": "M3U8 URL is required"}
            if not self.video_name:
                return {"status": False, "message": "Video name is not set"}

            # Gunakan master URL agar ffplay bisa pilih stream sendiri
            play_url = self._master_m3u8_url or self.m3u8_url

            ffplay_command = [
                "ffplay",
                "-i", play_url,
                "-window_title", self.video_name,
            ]

            subtitle_path = self.video_name.replace(" ", "_") + ".srt"
            if self.is_subtitle and os.path.exists(subtitle_path):
                ffplay_command.extend(["-vf", f"subtitles={subtitle_path}"])

            ffplay_command.extend(["-hide_banner", "-loglevel", "panic"])

            subprocess.call(ffplay_command)

            # Cleanup subtitle files
            if self.is_subtitle:
                vtt_path = self.video_name.replace(" ", "_") + ".vtt"
                if os.path.exists(subtitle_path):
                    os.remove(subtitle_path)
                if os.path.exists(vtt_path):
                    os.remove(vtt_path)

            return {"status": True, "message": "Playing m3u8"}

        except Exception as e:
            return {"status": False, "message": str(e)}

    # ------------------------------------------------------------------
    # VTT to SRT converter
    # ------------------------------------------------------------------
    @staticmethod
    def convert_vtt_to_srt(vtt_file: str) -> None:
        convert_file = ConvertFile(vtt_file, "utf-8")
        convert_file.convert()


# ======================================================================
# Standalone helper for main.py (process_movie tetap di main.py)
# ======================================================================
    # ------------------------------------------------------------------
    # Split Video  (FR-10: TikTok/Shorts Ready via FFmpeg Segment Muxer)
    # ------------------------------------------------------------------
    def split_video(self, input_file: str, segment_time: int = 600) -> dict[str, Any]:
        """
        Memotong video utuh menjadi beberapa part menggunakan ffmpeg.
        Metode default stream copy (tanpa re-encode) sehingga instan.
        Namun jika ada file `.srt` bernama sama, akan dilakukan HARDSUB
        ke dalam part-part video (membutuhkan re-encode).
        """
        try:
            self._ensure_ffmpeg()
            if not os.path.exists(input_file):
                return {"status": False, "message": f"Input file not found: {input_file}"}

            dir_name = os.path.dirname(input_file)
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            
            # Format output: folder_asli/NamaFilm_part_001.mp4
            output_pattern = os.path.join(dir_name, f"{base_name}_part_%03d.mp4")

            srt_file = os.path.join(dir_name, f"{base_name}.srt")
            has_srt = os.path.exists(srt_file)

            cmd = [
                "ffmpeg",
                "-y",
                "-i", input_file,
            ]

            if has_srt:
                logger.info("Subtitles found! Performing Hardsub (This will take longer due to re-encoding)...")
                # Format path untuk filter subtitles ffmpeg (escape backslash and colon)
                safe_srt = srt_file.replace("\\", "/").replace(":", "\\:")
                cmd.extend([
                    "-vf", f"subtitles='{safe_srt}'",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-c:a", "copy"
                ])
            else:
                logger.info("No subtitles found. Performing fast stream copy...")
                cmd.extend(["-c", "copy"])

            cmd.extend([
                "-map", "0",
                "-segment_time", str(segment_time),
                "-segment_start_number", "1",
                "-f", "segment",
                "-reset_timestamps", "1",
                output_pattern
            ])

            logger.info(f"Splitting video '{base_name}' into {segment_time}s parts...")
            logger.debug(f"ffmpeg command: {' '.join(cmd)}")

            import subprocess
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.success(f"Video successfully split: {base_name}_part_XXX.mp4")
                return {"status": True, "message": "Video split successfully"}
            else:
                logger.error(f"FFMPEG Error: {result.stderr}")
                return {
                    "status": False,
                    "message": f"ffmpeg exited with code {result.returncode}",
                    "error": result.stderr,
                }
        except Exception as e:
            return {"status": False, "message": str(e)}
