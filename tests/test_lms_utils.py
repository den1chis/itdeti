from core.lms_utils import get_embed_url, is_http_url

FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def test_drive_file_view_link_becomes_preview():
    url = f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing"
    assert get_embed_url(url) == f"https://drive.google.com/file/d/{FILE_ID}/preview"


def test_drive_open_and_uc_links():
    assert get_embed_url(f"https://drive.google.com/open?id={FILE_ID}") == f"https://drive.google.com/file/d/{FILE_ID}/preview"
    assert get_embed_url(f"https://drive.google.com/uc?id={FILE_ID}&export=download") == f"https://drive.google.com/file/d/{FILE_ID}/preview"


def test_drive_folder_link():
    url = f"https://drive.google.com/drive/folders/{FILE_ID}?usp=sharing"
    assert get_embed_url(url) == f"https://drive.google.com/embeddedfolderview?id={FILE_ID}#list"


def test_google_docs_types():
    for kind in ("document", "presentation", "spreadsheets"):
        url = f"https://docs.google.com/{kind}/d/{FILE_ID}/edit?usp=sharing"
        assert get_embed_url(url) == f"https://docs.google.com/{kind}/d/{FILE_ID}/preview"


def test_unknown_or_unsafe_links_are_not_embedded():
    assert get_embed_url(None) is None
    assert get_embed_url("") is None
    assert get_embed_url("https://example.com/file/d/" + FILE_ID) is None
    assert get_embed_url(f"http://drive.google.com/file/d/{FILE_ID}/view") is None  # not https
    assert get_embed_url("https://drive.google.com.evil.com/file/d/" + FILE_ID + "/view") is None
    assert get_embed_url("https://drive.google.com/file/d/short/view") is None
    assert get_embed_url("javascript:alert(1)") is None


def test_is_http_url():
    assert is_http_url("https://drive.google.com/x")
    assert is_http_url("http://example.com")
    assert not is_http_url("javascript:alert(1)")
    assert not is_http_url("data:text/html;base64,AAAA")
    assert not is_http_url("file:///etc/passwd")
    assert not is_http_url("ftp://example.com")
    assert not is_http_url("https://")
