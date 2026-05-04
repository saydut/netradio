#!/usr/bin/env python
import asyncio
import json
import os
import signal
import subprocess
import tomllib
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static, TabbedContent, TabPane

from youtubesearchpython import VideosSearch
from youtubesearchpython.core import requests as _ysp_req
import httpx as _httpx
from youtubesearchpython.core.constants import userAgent as _ua

# httpx 0.28+ uyumluluk yaması
def _p(self):
    p = self.proxy.get("https://") or self.proxy.get("http://")
    return _httpx.post(self.url, headers={"User-Agent": _ua}, json=self.data, timeout=self.timeout, proxy=p)

def _g(self):
    p = self.proxy.get("https://") or self.proxy.get("http://")
    return _httpx.get(self.url, headers={"User-Agent": _ua}, timeout=self.timeout, cookies={"CONSENT": "YES+1"}, proxy=p)

_ysp_req.RequestCore.syncPostRequest = _p
_ysp_req.RequestCore.syncGetRequest = _g

MPV_SOCKET = "/tmp/netradio-mpv.sock"
CONFIG_PATH = Path.home() / ".config" / "netradio" / "stations.toml"

DEFAULT_STATIONS = """\
[[stations]]
name = "KRAL Pop"
url = "http://46.20.3.201:80/;"

[[stations]]
name = "Power Türk"
url = "https://live.powerapp.com.tr/powerturk/abr/playlist.m3u8"

[[stations]]
name = "Alem"
url = "https://turkmedya.radyotvonline.com/turkmedya/alemfm.stream/playlist.m3u8"

[[stations]]
name = "Joy"
url = "http://provisioning.streamtheworld.com/pls/JOY_FMAAC.pls"

[[stations]]
name = "Power"
url = "http://icast.powergroup.com.tr/PowerTurk/mpeg/128/home"

[[stations]]
name = "Slow Türk"
url = "https://radyo.duhnet.tv/slowturk"

[[stations]]
name = "Pal"
url = "http://shoutcast.radyogrup.com:1030/;"

[[stations]]
name = "Powerturk"
url = "http://mpegpowerturk.listenpowerapp.com/powerturk/mpeg/icecast.audio"
"""

def load_stations():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(DEFAULT_STATIONS)
    with open(CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)
    return [(s["name"], s["url"]) for s in data.get("stations", [])]

mpv_process = None

def _kill_mpv():
    global mpv_process
    if mpv_process:
        try:
            os.killpg(os.getpgid(mpv_process.pid), signal.SIGTERM)
            mpv_process.wait()
        except Exception:
            pass
        mpv_process = None

def play_radio(url: str):
    global mpv_process
    _kill_mpv()
    cmd = f"mpv --input-ipc-server={MPV_SOCKET} --no-video {url}"
    mpv_process = subprocess.Popen(cmd, shell=True, preexec_fn=os.setsid,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

YOUTUBE_FORMATS = [
    ("Sadece ses (en iyi)", ["--no-video", "--ytdl-format=bestaudio"]),
    ("Video — en iyi", []),
    ("Video — 1080p", ["--ytdl-format=bestvideo[height<=1080]+bestaudio/best[height<=1080]"]),
    ("Video — 720p", ["--ytdl-format=bestvideo[height<=720]+bestaudio/best[height<=720]"]),
    ("Video — 480p", ["--ytdl-format=bestvideo[height<=480]+bestaudio/best[height<=480]"]),
    ("Video — 360p", ["--ytdl-format=bestvideo[height<=360]+bestaudio/best[height<=360]"]),
]

def play_youtube(url: str, extra_args: list[str]):
    global mpv_process
    _kill_mpv()
    mpv_process = subprocess.Popen(
        ["mpv", f"--input-ipc-server={MPV_SOCKET}", *extra_args, url],
        preexec_fn=os.setsid,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

async def get_mpv_title() -> str:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(MPV_SOCKET), timeout=1.0
        )
        writer.write((json.dumps({"command": ["get_property", "media-title"]}) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        writer.close()
        await writer.wait_closed()
        return json.loads(line).get("data") or ""
    except Exception:
        return ""


class FormatPicker(ModalScreen[int]):
    BINDINGS = [Binding("escape", "dismiss(None)", "İptal")]

    DEFAULT_CSS = """
    FormatPicker {
        align: center middle;
    }
    FormatPicker > Vertical {
        background: $surface;
        border: tall $primary;
        padding: 1 2;
        width: 50;
        height: auto;
    }
    FormatPicker ListView {
        height: auto;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Format seçin:")
            yield ListView(
                *[ListItem(Label(name)) for name, _ in YOUTUBE_FORMATS],
                id="format-list",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.list_view.index)


class NowPlaying(Static):
    def on_mount(self):
        self.set_interval(2, self.refresh_title)

    async def refresh_title(self):
        title = await get_mpv_title()
        if title:
            self.update(f"♪  {title}")
        elif mpv_process and mpv_process.poll() is None:
            self.update("♪  Yükleniyor...")
        else:
            self.update("♪  —")


class NetRadioApp(App):
    CSS = """
    Screen { background: $surface; }

    TabbedContent { height: 1fr; }

    ListView {
        height: 1fr;
        border: tall $primary;
    }

    ListView > ListItem { padding: 0 2; }

    Input { margin: 1 0; }

    #search-status {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }

    NowPlaying {
        background: $primary-darken-2;
        color: $text;
        padding: 0 2;
        height: 1;
        dock: bottom;
    }
    """

    BINDINGS = [
        Binding("s", "stop", "Durdur"),
        Binding("q", "quit", "Çıkış"),
    ]

    def __init__(self):
        super().__init__()
        self.stations = load_stations()
        self.search_results: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            with TabPane("📻 Radyo", id="radio-tab"):
                yield ListView(
                    *[ListItem(Label(name)) for name, _ in self.stations],
                    id="station-list",
                )
            with TabPane("🎵 YouTube", id="youtube-tab"):
                with Vertical():
                    yield Input(placeholder="Ara ve Enter'a bas...", id="search-input")
                    yield Label("", id="search-status")
                    yield ListView(id="result-list")
        yield NowPlaying("♪  —")
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None:
            return
        if event.list_view.id == "station-list":
            name, url = self.stations[idx]
            self.query_one(NowPlaying).update(f"♪  {name} yükleniyor...")
            self.run_worker(lambda u=url: play_radio(u), thread=True, exclusive=True)
        elif event.list_view.id == "result-list" and self.search_results:
            title, url = self.search_results[idx]
            def _on_pick(fmt_idx: int | None) -> None:
                if fmt_idx is None:
                    return
                _, args = YOUTUBE_FORMATS[fmt_idx]
                self.query_one(NowPlaying).update(f"♪  {title} yükleniyor...")
                self.run_worker(lambda u=url, a=args: play_youtube(u, a), thread=True, exclusive=True)
            self.push_screen(FormatPicker(), _on_pick)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        term = event.value.strip()
        if term:
            self._do_search(term)

    @work(thread=True)
    def _do_search(self, term: str) -> None:
        self.call_from_thread(
            self.query_one("#search-status", Label).update,
            f"🔍  '{term}' aranıyor...",
        )
        try:
            results = VideosSearch(term, limit=10).result()["result"]
            self.search_results = [(r["title"], r["link"]) for r in results]
            self.call_from_thread(self._populate_results)
        except Exception as e:
            self.call_from_thread(
                self.query_one("#search-status", Label).update,
                f"Hata: {e}",
            )

    def _populate_results(self) -> None:
        lv = self.query_one("#result-list", ListView)
        lv.clear()
        for title, _ in self.search_results:
            lv.append(ListItem(Label(title)))
        self.query_one("#search-status", Label).update(
            f"{len(self.search_results)} sonuç bulundu"
        )

    def action_stop(self) -> None:
        _kill_mpv()
        self.query_one(NowPlaying).update("♪  —")

    def action_quit(self) -> None:
        _kill_mpv()
        self.exit()

    def on_unmount(self) -> None:
        _kill_mpv()


if __name__ == "__main__":
    NetRadioApp().run()
