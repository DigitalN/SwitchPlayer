import io
import json
import time

import pytest

import titledb

ZELDA, RHYTHM, MARIO = "01008CF01BAAC000", "0100D9F01D474000", "0100965017338000"
JPEG = b"\xff\xd8\xff\xe0 not really a picture"

# titledb's shape: {nsuId: entry}, pretty-printed, a title listed twice (a later edition has a
# higher nsuId), entries without an id, and a non-ASCII name.
LIST = {
    "70010000096825": {"id": MARIO, "name": "Super Mario Party™ Jamboree – Nintendo Switch™ 2 Edition + Jamboree TV",
                       "iconUrl": "https://img-eshop.cdn.nintendo.net/i/edition.jpg"},
    "70010000084608": {"id": MARIO, "name": "Super Mario Party™ Jamboree", "iconUrl": "https://img-eshop.cdn.nintendo.net/i/mario.jpg"},
    "70010000083001": {"id": ZELDA, "name": "The Legend of Zelda™: Echoes of Wisdom", "iconUrl": "https://img-eshop.cdn.nintendo.net/i/zelda.jpg",
                       "screenshots": ["a", "b"], "nested": {"deep": [1, {"x": "}{,:"}]}},
    "70010000000001": {"id": None, "name": "No ID"},
    "70010000000002": {"name": "ポケモン"},
    "70010000090000": {"id": RHYTHM.lower(), "name": "  Rhythm Heaven®  Groove ", "iconUrl": "http://img-eshop.cdn.nintendo.net/i/rhythm.jpg"},
}


def stream(obj, indent=4):
    return io.BytesIO(json.dumps(obj, indent=indent, ensure_ascii=False).encode("utf-8"))


@pytest.mark.parametrize("chunk", [1, 7, 64, 1 << 20])
@pytest.mark.parametrize("indent", [None, 4])
def test_reads_every_entry_across_buffer_boundaries(chunk, indent):
    got = list(titledb.entries(stream(LIST, indent), chunk=chunk))
    assert got == list(LIST.items())


def test_empty_and_broken_lists():
    assert list(titledb.entries(io.BytesIO(b" { } "))) == []
    with pytest.raises(ValueError):
        list(titledb.entries(io.BytesIO(b"[1, 2]")))
    with pytest.raises(ValueError):
        list(titledb.entries(io.BytesIO(b'{"a": {"id": "x"'), chunk=4))


def test_keeps_the_original_listing_and_cleans_names():
    found = titledb.titles_in(stream(LIST), {ZELDA, RHYTHM, MARIO, "0100000000010000"})
    assert found[MARIO] == {"name": "Super Mario Party Jamboree", "icon": "https://img-eshop.cdn.nintendo.net/i/mario.jpg"}
    assert found[ZELDA]["name"] == "The Legend of Zelda: Echoes of Wisdom"
    assert found[RHYTHM]["name"] == "Rhythm Heaven Groove"  # the list's lower-case id, matched
    assert "0100000000010000" not in found


def test_images_only_from_nintendos_cdn_over_https():
    assert titledb.allowed_image("https://img-eshop.cdn.nintendo.net/i/x.jpg")
    assert not titledb.allowed_image("http://img-eshop.cdn.nintendo.net/i/x.jpg")
    assert not titledb.allowed_image("https://img-eshop.cdn.nintendo.net.example.com/i/x.jpg")
    assert not titledb.allowed_image("https://example.com/i/x.jpg")
    assert not titledb.allowed_image("file:///etc/passwd")


class FakeNet:
    """Serves the list and images from memory, counting downloads."""

    def __init__(self, listing=LIST, images=None):
        self.listing, self.images, self.calls = listing, images or {}, []

    def __call__(self, url, output, max_time):
        self.calls.append(url)
        if url == titledb.LIST_URL:
            if self.listing is None:
                return False
            output.write_bytes(json.dumps(self.listing, indent=4).encode())
            return True
        if url in self.images:
            output.write_bytes(self.images[url])
            return True
        return False


def test_downloads_names_and_art_once(tmp_path):
    net = FakeNet(images={"https://img-eshop.cdn.nintendo.net/i/mario.jpg": JPEG,
                          "https://img-eshop.cdn.nintendo.net/i/zelda.jpg": JPEG})
    db = titledb.TitleDB(tmp_path, download=net)
    assert db.update([MARIO, ZELDA]) == {MARIO, ZELDA}
    assert db.name(MARIO) == "Super Mario Party Jamboree"
    assert db.art(MARIO).read_bytes() == JPEG
    assert not (tmp_path / "US.en.json.download").exists()  # only the Library's titles are kept
    assert net.calls.count(titledb.LIST_URL) == 1

    again = titledb.TitleDB(tmp_path, download=net)  # the next launch
    assert again.update([MARIO, ZELDA]) == set()
    assert net.calls.count(titledb.LIST_URL) == 1  # everything was known


def test_a_title_the_list_lacks_waits_a_week(tmp_path):
    net = FakeNet()
    titledb.TitleDB(tmp_path, download=net).update(["0100000000010000"])
    titledb.TitleDB(tmp_path, download=net).update(["0100000000010000"])
    assert net.calls.count(titledb.LIST_URL) == 1
    index = json.loads((tmp_path / "index.json").read_text())
    index["fetched"] = time.time() - titledb.MISSING_LIFETIME - 60
    (tmp_path / "index.json").write_text(json.dumps(index))
    titledb.TitleDB(tmp_path, download=net).update(["0100000000010000"])
    assert net.calls.count(titledb.LIST_URL) == 2


def test_a_new_game_brings_the_list_back_once_a_launch(tmp_path):
    net = FakeNet(listing=None)  # offline
    db = titledb.TitleDB(tmp_path, download=net)
    assert db.update([MARIO]) == set()
    assert db.update([MARIO, ZELDA]) == set()
    assert net.calls.count(titledb.LIST_URL) == 1
    assert db.name(MARIO) is None and db.art(MARIO) is None


def test_refuses_art_from_elsewhere_and_anything_not_a_jpeg(tmp_path):
    listing = {"1": {"id": RHYTHM, "name": "Rhythm", "iconUrl": "http://img-eshop.cdn.nintendo.net/i/rhythm.jpg"},
               "2": {"id": MARIO, "name": "Mario", "iconUrl": "https://img-eshop.cdn.nintendo.net/i/mario.jpg"}}
    net = FakeNet(listing, images={"http://img-eshop.cdn.nintendo.net/i/rhythm.jpg": JPEG,
                                   "https://img-eshop.cdn.nintendo.net/i/mario.jpg": b"<html>rate limited</html>"})
    db = titledb.TitleDB(tmp_path, download=net)
    assert db.update([RHYTHM, MARIO]) == {RHYTHM, MARIO}  # their names are new
    assert "http://img-eshop.cdn.nintendo.net/i/rhythm.jpg" not in net.calls
    assert db.art(RHYTHM) is None and db.art(MARIO) is None
    assert not list((tmp_path / "art").glob("*.partial"))


def test_a_damaged_index_is_fetched_again(tmp_path):
    (tmp_path / "index.json").write_text("{not json")
    net = FakeNet()
    db = titledb.TitleDB(tmp_path, download=net)
    db.update([ZELDA])
    assert db.name(ZELDA) == "The Legend of Zelda: Echoes of Wisdom"


def test_clean_name():
    assert titledb.clean_name("Pokémon™ Legends: Z-A") == "Pokémon Legends: Z-A"
    assert titledb.clean_name(" ™ ") is None
    assert titledb.clean_name(None) is None
