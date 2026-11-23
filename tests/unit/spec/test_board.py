import base64
from pathlib import Path

import pytest

from agent_harness.spec.board import BoardError, linked_files, page_source

FOLDER = "docs/specs"
PNG = b"\x89PNG\r\n\x1a\nfake"


def put(root: Path, name: str, data: bytes | str) -> None:
    path = root / FOLDER / "assets" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def board_text(uri: str | None) -> str:
    assert uri is not None
    head = "data:image/svg+xml;base64,"
    assert uri.startswith(head)
    return base64.b64decode(uri[len(head) :]).decode("utf-8")


def uri_of(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def test_writes_a_linked_raster_file_into_the_board_as_a_data_uri(tmp_path: Path) -> None:
    put(tmp_path, "shot.png", PNG)
    put(tmp_path, "board.svg", '<svg><image href="shot.png" width="4" height="4"/></svg>')

    text = board_text(page_source(tmp_path, FOLDER, "assets/board.svg"))

    assert text == f'<svg><image href="{uri_of("image/png", PNG)}" width="4" height="4"/></svg>'


def test_reads_xlink_href_in_single_quotes_and_a_path_in_a_subfolder(tmp_path: Path) -> None:
    put(tmp_path, "art/a%20b.jpg", b"jpeg")
    put(tmp_path, "art/a b.jpg", b"jpeg")
    put(tmp_path, "board.svg", "<svg><image x='0' xlink:href='art/a%20b.jpg'/></svg>")

    text = board_text(page_source(tmp_path, FOLDER, "assets/board.svg"))

    assert text == f"<svg><image x='0' xlink:href='{uri_of('image/jpeg', b'jpeg')}'/></svg>"


def test_resolves_a_link_against_the_folder_of_the_board(tmp_path: Path) -> None:
    put(tmp_path, "flow/shot.png", PNG)
    put(tmp_path, "flow/board.svg", '<svg><image href="shot.png"/></svg>')

    text = board_text(page_source(tmp_path, FOLDER, "assets/flow/board.svg"))

    assert uri_of("image/png", PNG) in text


def test_writes_a_linked_svg_with_its_own_linked_files(tmp_path: Path) -> None:
    put(tmp_path, "shot.png", PNG)
    inner = '<svg><image href="shot.png"/></svg>'
    put(tmp_path, "inner.svg", inner)
    put(tmp_path, "board.svg", '<svg><image href="inner.svg#top"/></svg>')

    text = board_text(page_source(tmp_path, FOLDER, "assets/board.svg"))

    inner_page = inner.replace("shot.png", uri_of("image/png", PNG)).encode("utf-8")
    assert text == f'<svg><image href="{uri_of("image/svg+xml", inner_page)}#top"/></svg>'


def test_gives_no_page_source_for_a_board_that_links_no_file(tmp_path: Path) -> None:
    svg = (
        '<svg><image href="data:image/png;base64,AAAA"/><image href="https://x.test/a.png"/>'
        '<use href="#mark"/><a href="shot.png">t</a></svg>'
    )
    put(tmp_path, "board.svg", svg)

    assert page_source(tmp_path, FOLDER, "assets/board.svg") is None


def test_leaves_the_source_board_as_it_is(tmp_path: Path) -> None:
    put(tmp_path, "shot.png", PNG)
    svg = '<svg><image href="shot.png"/></svg>'
    put(tmp_path, "board.svg", svg)

    page_source(tmp_path, FOLDER, "assets/board.svg")

    assert (tmp_path / FOLDER / "assets/board.svg").read_text(encoding="utf-8") == svg


def test_refuses_a_linked_file_that_does_not_exist(tmp_path: Path) -> None:
    put(tmp_path, "board.svg", '<svg><image href="art/shot.png"/></svg>')

    with pytest.raises(BoardError) as error:
        page_source(tmp_path, FOLDER, "assets/board.svg")

    assert str(error.value) == (
        "the board assets/board.svg links art/shot.png, which has no file at "
        "docs/specs/assets/art/shot.png"
    )


def test_refuses_a_linked_file_outside_the_assets_folder(tmp_path: Path) -> None:
    (tmp_path / FOLDER).mkdir(parents=True)
    (tmp_path / FOLDER / "secret.png").write_bytes(PNG)
    put(tmp_path, "board.svg", '<svg><image href="../secret.png"/></svg>')

    with pytest.raises(BoardError) as error:
        page_source(tmp_path, FOLDER, "assets/board.svg")

    assert str(error.value) == (
        "the board assets/board.svg links ../secret.png, which is outside docs/specs/assets"
    )


def test_refuses_a_linked_file_of_an_unknown_picture_type(tmp_path: Path) -> None:
    put(tmp_path, "notes.txt", "text")
    put(tmp_path, "board.svg", '<svg><image href="notes.txt"/></svg>')

    with pytest.raises(BoardError) as error:
        page_source(tmp_path, FOLDER, "assets/board.svg")

    assert str(error.value) == (
        "the board assets/board.svg links notes.txt, which is not a PNG, JPEG, GIF, WebP, AVIF "
        "or SVG file"
    )


def test_refuses_a_board_that_links_itself_through_another_board(tmp_path: Path) -> None:
    put(tmp_path, "a.svg", '<svg><image href="b.svg"/></svg>')
    put(tmp_path, "b.svg", '<svg><image href="a.svg"/></svg>')

    with pytest.raises(BoardError) as error:
        page_source(tmp_path, FOLDER, "assets/a.svg")

    assert str(error.value) == "the board assets/b.svg links a.svg, which links it back"


def test_lists_the_files_that_a_board_links() -> None:
    svg = (
        '<svg><image href="art/shot.png"/><image xlink:href="b.svg#top"/>'
        '<image href="data:image/png;base64,AAAA"/><image href="https://x.test/a.png"/></svg>'
    )

    assert linked_files(svg) == ["art/shot.png", "b.svg"]


def test_decodes_the_percent_escapes_of_a_linked_path() -> None:
    assert linked_files('<svg><image href="art/my%20shot.png#top"/></svg>') == ["art/my shot.png"]


def test_leaves_out_an_image_element_inside_an_xml_comment() -> None:
    comment = '<!-- <image href="old.png"/>\n<image href="older.png"/> -->'
    svg = f'<svg>{comment}<image href="a.png"/></svg>'

    assert linked_files(svg) == ["a.png"]


def test_leaves_an_image_element_inside_an_xml_comment_as_it_is(tmp_path: Path) -> None:
    put(tmp_path, "shot.png", PNG)
    comment = '<!-- <image href="old.png"/> -->'
    put(tmp_path, "board.svg", f'<svg>{comment}<image href="shot.png"/></svg>')

    text = board_text(page_source(tmp_path, FOLDER, "assets/board.svg"))

    assert text == f'<svg>{comment}<image href="{uri_of("image/png", PNG)}"/></svg>'


def test_gives_no_page_source_for_a_board_whose_only_link_is_in_an_xml_comment(
    tmp_path: Path,
) -> None:
    put(tmp_path, "board.svg", '<svg><!-- <image href="old.png"/> --></svg>')

    assert page_source(tmp_path, FOLDER, "assets/board.svg") is None
