"""Mała kopia plakatu dla list Lumena."""

from __future__ import annotations

from PIL import Image

from emustart.api import Api


def test_thumb_is_small_and_keeps_shape(tmp_path):
    src, dst = tmp_path / "a.png", tmp_path / "t" / "a.webp"
    Image.new("RGB", (600, 900), (10, 20, 30)).save(src)
    assert Api.art_thumb(object(), str(src), str(dst), 540) == {"ok": True}
    with Image.open(dst) as im:
        assert im.size == (360, 540) and im.format == "WEBP"


def test_not_an_image_gives_up_without_a_file(tmp_path):
    src, dst = tmp_path / "a.png", tmp_path / "a.webp"
    src.write_bytes(b"nie obraz")
    assert Api.art_thumb(object(), str(src), str(dst), 540) == {"ok": False}
    assert not dst.exists()


def test_huge_animation_becomes_first_frame_small_stays(tmp_path, monkeypatch):
    from emustart import api as mod
    src, dst = tmp_path / "a.webp", tmp_path / "a-t.webp"
    Image.new("RGB", (60, 90), (1, 2, 3)).save(src, save_all=True, append_images=[Image.new("RGB", (60, 90), (9, 9, 9))])
    assert Api.art_thumb(object(), str(src), str(dst), 45) == {"ok": False}      # mała: animacja zostaje
    monkeypatch.setattr(mod, "ANIMATED_ON_LIST_MAX", 1)
    assert Api.art_thumb(object(), str(src), str(dst), 45) == {"ok": True}
    with Image.open(dst) as im:
        assert not getattr(im, "is_animated", False)
