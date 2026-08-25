from src.idlixHelper import IdlixHelper, logger
from prettytable import PrettyTable
from urllib.parse import urlparse
import inquirer
import re
import threading
import time
import os

RETRY_LIMIT = 3


def _has_display() -> bool:
    """True when a GUI display is available (not headless server)."""
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def ask_directory(title: str = "Pilih Lokasi Download") -> str:
    """Pick a folder via GUI if available, otherwise prompt in terminal."""
    if _has_display():
        try:
            from tkinter import filedialog
            return filedialog.askdirectory(title=title) or os.getcwd()
        except Exception:
            pass
    answer = inquirer.prompt([
        inquirer.Text(
            "path",
            message=f"{title} (kosongkan = folder saat ini)",
            default=os.getcwd(),
        )
    ])
    path = (answer or {}).get("path") or os.getcwd()
    return os.path.abspath(os.path.expanduser(path.strip()))


def ask_open_filename(title: str = "Pilih File", filetypes=None) -> str | None:
    """Pick a file via GUI if available, otherwise prompt in terminal."""
    if _has_display():
        try:
            from tkinter import filedialog
            kwargs = {"title": title}
            if filetypes:
                kwargs["filetypes"] = filetypes
            return filedialog.askopenfilename(**kwargs) or None
        except Exception:
            pass
    answer = inquirer.prompt([
        inquirer.Text("path", message=f"{title} (path file)")
    ])
    path = ((answer or {}).get("path") or "").strip()
    if not path:
        return None
    return os.path.abspath(os.path.expanduser(path))


def retry(func, *args, **kwargs):
    for _ in range(RETRY_LIMIT):
        result = func(*args, **kwargs)
        if result and result.get("status"):
            return result
        time.sleep(1)
    return {"status": False, "message": "Maximum retry reached"}


def play_m3u8_thread(idlix_helper):
    result = idlix_helper.play_m3u8()
    if result.get("status"):
        logger.success("Playing Success")
    else:
        logger.error("Error playing m3u8")


def process_series_batch(idlix_helper, url: str | None = None):
    """Batch download TV series episodes per season."""
    if not url:
        url = input("Masukkan URL / Slug TV Series (misal: https://tv7.idlix.if.ua/series/game-of-thrones-2011): ").strip()
        if not url:
            logger.error("URL tidak boleh kosong")
            return

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    slug = path_parts[1] if len(path_parts) >= 2 else (path_parts[0] if path_parts else url)

    logger.info(f"Mengambil informasi series: {slug} ...")
    series_info = idlix_helper.get_series_info(slug)
    if not series_info.get("status"):
        logger.error(f"Gagal mengambil informasi series: {series_info.get('message')}")
        return

    series_title = series_info.get("title") or slug
    seasons = series_info.get("seasons", [])
    if not seasons:
        logger.error("Tidak ada Season ditemukan untuk series ini.")
        return

    logger.success(f"Series: {series_title} | Total Season: {len(seasons)}")

    season_choices = [
        f"Season {s['season_number']} ({s['episode_count']} Episodes)" for s in seasons
    ]
    q_season = [
        inquirer.List(
            "season_choice",
            message="Pilih Season yang ingin di-download",
            choices=season_choices,
            carousel=True
        )
    ]
    ans_season = inquirer.prompt(q_season)
    if not ans_season:
        return

    sel_index = season_choices.index(ans_season["season_choice"])
    selected_season = seasons[sel_index]
    season_num = selected_season["season_number"]

    logger.info(f"Mengambil daftar episode untuk Season {season_num}...")
    season_ep_res = idlix_helper.get_season_episodes(slug, season_num)
    if not season_ep_res.get("status"):
        logger.error(f"Gagal mengambil episode: {season_ep_res.get('message')}")
        return

    episodes = season_ep_res.get("episodes", [])
    if not episodes:
        logger.error(f"Tidak ada episode ditemukan di Season {season_num}.")
        return

    q_mode = [
        inquirer.List(
            "ep_mode",
            message=f"Pilih mode download Episode untuk Season {season_num} ({len(episodes)} episode)",
            choices=[
                "Semua Episode (All Episodes)",
                "Rentang Episode (Episode Range, contoh: 1-5)",
                "Pilih Episode Spesifik (Multi-select)"
            ],
            carousel=True
        )
    ]
    ans_mode = inquirer.prompt(q_mode)
    if not ans_mode:
        return

    selected_episodes = []
    ep_mode = ans_mode["ep_mode"]

    if "Semua" in ep_mode:
        selected_episodes = episodes
    elif "Rentang" in ep_mode:
        range_ans = inquirer.prompt([
            inquirer.Text("range", message=f"Masukkan rentang (1-{len(episodes)})", default=f"1-{len(episodes)}")
        ])
        if range_ans and range_ans.get("range"):
            raw_range = range_ans["range"].strip()
            try:
                if "-" in raw_range:
                    start_e, end_e = map(int, raw_range.split("-"))
                else:
                    start_e = end_e = int(raw_range)
                selected_episodes = [
                    ep for ep in episodes
                    if ep["episode_number"] is not None and start_e <= ep["episode_number"] <= end_e
                ]
            except ValueError:
                logger.error("Format rentang tidak valid, mendownload semua episode.")
                selected_episodes = episodes
    else:
        ep_choices = [
            (f"E{ep['episode_number']:02d}: {ep['title']}", ep)
            for ep in episodes
        ]
        q_check = [
            inquirer.Checkbox(
                "selected_eps",
                message="Pilih episode yang ingin di-download (gunakan spasi untuk memilih)",
                choices=[c[0] for c in ep_choices]
            )
        ]
        ans_check = inquirer.prompt(q_check)
        if ans_check and ans_check.get("selected_eps"):
            selected_titles = set(ans_check["selected_eps"])
            selected_episodes = [c[1] for c in ep_choices if c[0] in selected_titles]

    if not selected_episodes:
        logger.warning("Tidak ada episode yang dipilih.")
        return

    q_qual = [
        inquirer.List(
            "quality_mode",
            message="Pilih kualitas video batch download",
            choices=[
                "Resolusi Tertinggi (Auto Best)",
                "720p (jika ada)",
                "480p (jika ada)",
                "Pilih Kualitas Manual per Episode"
            ],
            carousel=True
        )
    ]
    ans_qual = inquirer.prompt(q_qual)
    qual_mode = ans_qual["quality_mode"] if ans_qual else "Resolusi Tertinggi (Auto Best)"

    default_dir = ask_directory("Pilih Lokasi Download Utama")
    season_dir = os.path.join(default_dir, re.sub(r'[<>:"/\\|?*]', "", series_title).strip(), f"Season {season_num}")
    os.makedirs(season_dir, exist_ok=True)

    logger.info(f"\n=== Memulai Batch Download Season {season_num} ({len(selected_episodes)} Episode) ===")
    logger.info(f"Folder Output: {season_dir}\n")

    summary_results = []

    for idx, ep in enumerate(selected_episodes, start=1):
        ep_num = ep["episode_number"]
        ep_title = ep["title"]
        ep_url = ep["url"]

        logger.info(f"[{idx}/{len(selected_episodes)}] Processing S{season_num:02d}E{ep_num:02d} - {ep_title}")

        v_data = retry(idlix_helper.get_video_data, ep_url)
        if not v_data.get("status"):
            logger.error(f"Gagal mengambil data video E{ep_num}: {v_data.get('message')}")
            summary_results.append((f"S{season_num:02d}E{ep_num:02d}", "GAGAL (Data Video)"))
            continue

        embed = retry(idlix_helper.get_embed_url)
        if not embed.get("status"):
            logger.error(f"Gagal mengambil embed URL E{ep_num}: {embed.get('message')}")
            summary_results.append((f"S{season_num:02d}E{ep_num:02d}", "GAGAL (Embed URL)"))
            continue

        m3u8 = retry(idlix_helper.get_m3u8_url)
        if not m3u8.get("status"):
            logger.error(f"Gagal mengambil M3U8 URL E{ep_num}: {m3u8.get('message')}")
            summary_results.append((f"S{season_num:02d}E{ep_num:02d}", "GAGAL (M3U8)"))
            continue

        if m3u8.get("is_variant_playlist"):
            variants = m3u8["variant_playlist"]
            if "720" in qual_mode:
                match_v = next((v for v in variants if "720" in v["resolution"]), variants[0])
                idlix_helper.set_m3u8_url(match_v["uri"])
            elif "480" in qual_mode:
                match_v = next((v for v in variants if "480" in v["resolution"]), variants[0])
                idlix_helper.set_m3u8_url(match_v["uri"])
            elif "Manual" in qual_mode:
                choices = [f"{v['id']} - {v['resolution']}" for v in variants]
                ans_v = inquirer.prompt([
                    inquirer.List("variant", message=f"Pilih varian untuk E{ep_num}", choices=choices, carousel=True)
                ])
                if ans_v:
                    selected_id = ans_v["variant"].split(" - ")[0]
                    for v in variants:
                        if str(v["id"]) == selected_id:
                            idlix_helper.set_m3u8_url(v["uri"])
                            break
            else:
                idlix_helper.set_m3u8_url(variants[0]["uri"])

        sub_res = idlix_helper.get_subtitle(season_dir)
        if sub_res.get("status"):
            logger.success(f"Subtitle E{ep_num} berhasil diunduh")

        dl_res = idlix_helper.download_m3u8(season_dir)
        if dl_res.get("status"):
            logger.success(f"Download S{season_num:02d}E{ep_num:02d} BERHASIL!")
            summary_results.append((f"S{season_num:02d}E{ep_num:02d}", "SUKSES"))
        else:
            logger.error(f"Download S{season_num:02d}E{ep_num:02d} GAGAL: {dl_res.get('message')}")
            summary_results.append((f"S{season_num:02d}E{ep_num:02d}", "GAGAL (Download)"))

    table = PrettyTable()
    table.title = f"Ringkasan Batch Download - {series_title} Season {season_num}"
    table.field_names = ["Episode", "Status"]
    for item in summary_results:
        table.add_row([item[0], item[1]])
    print("\n")
    print(table)
    print("\n")


def process_movie(idlix_helper, url: str, mode: str):
    if "/series/" in url and "/episode/" not in url:
        logger.info("URL TV Series terdeteksi.")
        if mode == "download":
            q_type = [
                inquirer.List(
                    "series_mode",
                    message="Pilih opsi download untuk TV Series ini:",
                    choices=[
                        "Batch Download Season (Rekomendasi)",
                        "Download Single Episode"
                    ],
                    carousel=True
                )
            ]
            ans_type = inquirer.prompt(q_type)
            if ans_type and "Batch" in ans_type["series_mode"]:
                process_series_batch(idlix_helper, url)
                return

        q_series = [
            inquirer.Text('season', message="Masukkan nomor Season", default="1"),
            inquirer.Text('episode', message="Masukkan nomor Episode", default="1")
        ]
        ans_series = inquirer.prompt(q_series)
        if ans_series:
            url = url.rstrip("/")
            url = f"{url}/season/{ans_series['season'].strip()}/episode/{ans_series['episode'].strip()}"
            logger.info(f"Mengakses: {url}")

    video_data = retry(idlix_helper.get_video_data, url)
    if not video_data.get("status"):
        logger.error("Error getting video data")
        return

    logger.info(
        f"Getting video data | Video ID: {video_data['video_id']} | Video Name: {video_data['video_name']}"
    )

    embed = retry(idlix_helper.get_embed_url)
    if not embed.get("status"):
        logger.error("Error getting embed URL")
        return

    logger.success(f"Getting embed URL: {embed['embed_url']}")

    m3u8 = retry(idlix_helper.get_m3u8_url)
    if not m3u8.get("status"):
        logger.error("Error getting M3U8 URL")
        return

    logger.success(f"Getting m3u8 URL | {m3u8['m3u8_url']}")

    if m3u8.get("is_variant_playlist"):
        logger.warning("This video has a variant playlist")

        choices = [
            f"{v['id']} - {v['resolution']}" for v in m3u8["variant_playlist"]
        ]

        question = [
            inquirer.List(
                "variant",
                message="Select variant",
                choices=choices,
                carousel=True
            )
        ]
        answer = inquirer.prompt(question)

        selected_id = answer["variant"].split(" - ")[0]

        for v in m3u8["variant_playlist"]:
            if str(v["id"]) == selected_id:
                idlix_helper.set_m3u8_url(v["uri"])
                logger.success(f"Selected variant: {v['resolution']}")
                break
    else:
        logger.warning("This video has no variant playlist")

    # 5. If play → download subtitle
    if mode == "play":
        subtitle = idlix_helper.get_subtitle()
        if subtitle.get("status"):
            logger.success("Subtitle downloaded")
        else:
            logger.error("Subtitle unavailable")

        logger.info(f"Playing {video_data['video_name']} ...")

        th = threading.Thread(target=play_m3u8_thread, args=(idlix_helper,))
        th.daemon = True
        th.start()

        # avoid hang forever
        th.join(timeout=5)

    # 6. If download
    else:
        output_dir = ask_directory("Pilih Lokasi Download")

        # --- FIX: Download Subtitle langsung ke output_dir ---
        subtitle = idlix_helper.get_subtitle(output_dir)
        if subtitle.get("status"):
            logger.success("Subtitle downloaded for video")
        else:
            logger.warning("Subtitle unavailable for video")

        result = idlix_helper.download_m3u8(output_dir)
        if result.get("status"):
            logger.success(f"Downloading {video_data['video_name']} success")
            
            # --- FR-10: Optional Auto-split ---
            split_q = [
                inquirer.List(
                    "split",
                    message="Bagi video untuk TikTok/Shorts (10 menit per part)?",
                    choices=["Tidak", "Ya"],
                    carousel=True
                )
            ]
            ans_split = inquirer.prompt(split_q)
            if ans_split and ans_split["split"] == "Ya":
                safe_name = re.sub(r'[<>:"/\\|?*]', "", video_data['video_name']).strip()
                output_file = os.path.join(output_dir, f"{safe_name}.mp4")
                idlix_helper.split_video(output_file, segment_time=600)
                
        else:
            logger.error("Error downloading m3u8")


def show_featured_table(featured):
    table = PrettyTable()
    table.align = "l"
    table.title = "Featured Movie List"
    table.field_names = ["No", "Title", "Year", "Type", "URL"]

    for i, movie in enumerate(featured):
        table.add_row([
            i + 1,
            movie["title"],
            movie["year"],
            movie["type"],
            movie["url"]
        ])

    print(table)


def main():
    status_exit = False

    while not status_exit:
        idlix = IdlixHelper()
        home = retry(idlix.get_home)

        if not home.get("status") or len(home.get("featured_movie", [])) == 0:
            logger.error(f"Error fetching home: {home.get('message')}")
            break

        featured = home["featured_movie"]
        show_featured_table(featured)

        # Main Menu
        question = [
            inquirer.List(
                "action",
                message="Select action",
                choices=[
                    "Download Featured Movie",
                    "Play Featured Movie",
                    "Download Movie/Episode by URL",
                    "Batch Download Series (Per Season)",
                    "Play Movie by URL",
                    "Partisi Video (Lokal)",
                    "Exit"
                ],
                carousel=True
            )
        ]
        answer = inquirer.prompt(question)
        action = answer["action"]
        
        if action == "Partisi Video (Lokal)":
            file_path = ask_open_filename(
                title="Pilih Video untuk di-Partisi",
                filetypes=[("MP4 Files", "*.mp4"), ("All Files", "*.*")],
            )
            if file_path:
                idlix.split_video(file_path, segment_time=600)
            continue

        elif action == "Batch Download Series (Per Season)":
            process_series_batch(idlix)
            continue
            
        if action in ["Download Featured Movie", "Play Featured Movie"]:
            # Select movie
            movie_question = [
                inquirer.List(
                    "movie",
                    message="Select movie",
                    choices=[i["title"] for i in featured],
                    carousel=True
                )
            ]
            choice = inquirer.prompt(movie_question)

            selected = next(
                (m for m in featured if m["title"] == choice["movie"]),
                None
            )

            if not selected:
                logger.error("Movie not found")
                continue

            mode = "download" if "Download" in action else "play"
            process_movie(idlix, selected["url"], mode)


        elif action in ["Download Movie by URL", "Download Movie/Episode by URL"]:
            url = input("Enter movie or series URL: ").strip()
            process_movie(idlix, url, "download")

        elif action == "Play Movie by URL":
            url = input("Enter movie URL: ").strip()
            process_movie(idlix, url, "play")

        # Exit
        else:
            logger.info("Exiting...")
            status_exit = True


if __name__ == "__main__":
    main()
