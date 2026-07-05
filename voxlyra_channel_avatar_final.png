from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_final_avatar_assets_are_safe_square_pngs():
    paths = [
        ROOT / "static/img/bot_avatar.png",
        ROOT / "static/img/channel_avatar.png",
        ROOT / "voxlyra_bot_avatar_final.png",
        ROOT / "voxlyra_channel_avatar_final.png",
    ]
    for path in paths:
        assert path.exists(), path
        width, height = png_size(path)
        assert width == height
        assert width >= 512
        assert path.stat().st_size > 100_000


def test_standalone_avatar_files_match_embedded_copies():
    assert (ROOT / "voxlyra_bot_avatar_final.png").read_bytes() == (ROOT / "static/img/bot_avatar.png").read_bytes()
    assert (ROOT / "voxlyra_channel_avatar_final.png").read_bytes() == (ROOT / "static/img/channel_avatar.png").read_bytes()
