"""03-studio 追加 A · 素材上传（UP / UG / US）。

对应 `specs/03-studio/test_plan.md` 追加 A。
"""

from pathlib import Path

import pytest

from tests.helpers import assert_route_registered, bearer

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"payload"
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"\x00" * 12
WEBP = b"RIFF" + (16).to_bytes(4, "little") + b"WEBP" + b"VP8 " + b"\x00" * 4
GIF = b"GIF89a" + b"\x00" * 10
PDF = b"%PDF-1.4" + b"\x00" * 10
TXT_AS_JPG = b"this is plain text pretending to be jpg"


@pytest.fixture
def upload_dir(tmp_path, settings_override):
    d = tmp_path / "uploads"
    settings_override(upload_dir=str(d), upload_max_bytes=20 * 1024 * 1024)
    return d


async def _post_file(client, token, data, *, filename="a.png", content_type="image/png",
                     field="file"):
    files = {field: (filename, data, content_type)}
    headers = bearer(token) if token else {}
    return await client.post("/api/uploads", files=files, headers=headers)


# --------------------------------------------------------------------------- #
# UP · 上传
# --------------------------------------------------------------------------- #
async def test_up01_unauth(client, upload_dir):
    r = await _post_file(client, None, PNG)
    assert r.status_code == 401


async def test_up02_missing_file_field(client, customer, upload_dir):
    _, token, _ = customer
    r = await client.post(
        "/api/uploads",
        data={"note": "no file"},
        headers=bearer(token),
    )
    assert r.status_code == 422


async def test_up03_wrong_field_name(client, customer, upload_dir):
    _, token, _ = customer
    for field in ("upload", "image"):
        r = await _post_file(client, token, PNG, field=field)
        assert r.status_code == 422, field


async def test_up04_txt_renamed_jpg(client, customer, upload_dir):
    _, token, _ = customer
    r = await _post_file(
        client, token, TXT_AS_JPG, filename="x.jpg", content_type="image/jpeg"
    )
    assert r.status_code == 415


async def test_up05_gif_and_pdf(client, customer, upload_dir):
    _, token, _ = customer
    assert (await _post_file(client, token, GIF, filename="a.gif")).status_code == 415
    assert (await _post_file(client, token, PDF, filename="a.pdf")).status_code == 415


async def test_up06_exactly_20mb_png(client, customer, upload_dir):
    _, token, _ = customer
    payload = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + bytes(
        20 * 1024 * 1024 - 8
    )
    r = await _post_file(client, token, payload)
    assert r.status_code == 201
    assert r.json()["size_bytes"] == 20 * 1024 * 1024


async def test_up07_over_20mb(client, customer, upload_dir):
    _, token, _ = customer
    payload = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + bytes(
        20 * 1024 * 1024 - 7
    )
    r = await _post_file(client, token, payload)
    assert r.status_code == 413


async def test_up08_png_ok(client, customer, upload_dir):
    _, token, _ = customer
    r = await _post_file(client, token, PNG)
    assert r.status_code == 201
    body = r.json()
    assert body["mime"] == "image/png"
    assert body["size_bytes"] == len(PNG)
    assert body["url"].startswith("/api/uploads/")
    assert body["url"].endswith(".png")


async def test_up09_content_type_lie(client, customer, upload_dir):
    _, token, _ = customer
    r = await _post_file(
        client, token, PNG, filename="x.jpg", content_type="image/jpeg"
    )
    assert r.status_code == 201
    assert r.json()["mime"] == "image/png"


async def test_up10_empty_file(client, customer, upload_dir):
    _, token, _ = customer
    r = await _post_file(client, token, b"")
    assert r.status_code == 415


async def test_up11_path_traversal_filename(client, customer, upload_dir, tmp_path):
    _, token, _ = customer
    r = await _post_file(client, token, PNG, filename="../../etc/passwd")
    assert r.status_code == 201
    body = r.json()
    assert "passwd" not in body["url"]
    # 上传目录外不得出现新文件（相对 tmp_path 根）
    outside = list(tmp_path.rglob("passwd"))
    assert outside == []


async def test_up12_script_filename_not_echoed(client, customer, upload_dir):
    _, token, _ = customer
    r = await _post_file(client, token, PNG, filename="<script>x.png")
    assert r.status_code == 201
    body = r.json()
    assert "<script>" not in body["url"]
    assert "<script>" not in r.text


async def test_up13_no_dedup(client, customer, upload_dir):
    _, token, _ = customer
    a = (await _post_file(client, token, PNG)).json()
    b = (await _post_file(client, token, PNG)).json()
    assert a["url"] != b["url"]
    name_a = a["url"].rsplit("/", 1)[-1]
    name_b = b["url"].rsplit("/", 1)[-1]
    assert (upload_dir / name_a).is_file()
    assert (upload_dir / name_b).is_file()


async def test_up14_mkdir_missing_dir(client, customer, tmp_path, settings_override):
    missing = tmp_path / "nested" / "uploads"
    assert not missing.exists()
    settings_override(upload_dir=str(missing))
    _, token, _ = customer
    r = await _post_file(client, token, PNG)
    assert r.status_code == 201
    assert missing.is_dir()


async def test_up15_body_keys_exact(client, customer, upload_dir):
    _, token, _ = customer
    body = (await _post_file(client, token, PNG)).json()
    assert set(body.keys()) == {"url", "mime", "size_bytes"}


# --------------------------------------------------------------------------- #
# UG · 读取
# --------------------------------------------------------------------------- #
async def test_ug01_roundtrip_bytes(client, customer, upload_dir):
    _, token, _ = customer
    up = (await _post_file(client, token, PNG)).json()
    r = await client.get(up["url"])
    assert r.status_code == 200
    assert r.content == PNG


async def test_ug02_content_type_sniffed(client, customer, upload_dir):
    _, token, _ = customer
    up = (await _post_file(client, token, JPEG, filename="a.jpg")).json()
    r = await client.get(up["url"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")


async def test_ug03_missing_file(client, upload_dir):
    assert_route_registered("get", "/api/uploads/{name}")
    r = await client.get("/api/uploads/no-such-file.jpg")
    assert r.status_code == 404


async def test_ug04_path_traversal(client, upload_dir):
    r = await client.get("/api/uploads/..%2f..%2fconfig.py")
    assert r.status_code == 400
    assert b"database_url" not in r.content
    assert b"jwt_secret" not in r.content


async def test_ug05_slash_in_name(client, upload_dir):
    r = await client.get("/api/uploads/a%2fb.png")
    assert r.status_code == 400


async def test_ug06_no_extension(client, upload_dir):
    r = await client.get("/api/uploads/xxx")
    assert r.status_code == 400


async def test_ug07_no_auth_ok(client, customer, upload_dir):
    _, token, _ = customer
    up = (await _post_file(client, token, PNG)).json()
    r = await client.get(up["url"])  # 无 Authorization
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# US · sniff_mime 接缝
# --------------------------------------------------------------------------- #
def test_us01_sniff_known_magics():
    from app.services import storage

    assert storage.sniff_bytes(JPEG) == "image/jpeg"
    assert storage.sniff_bytes(PNG) == "image/png"
    assert storage.sniff_bytes(WEBP) == "image/webp"


def test_us02_sniff_unknown():
    from app.services import storage

    assert storage.sniff_bytes(b"") is None
    assert storage.sniff_bytes(b"abc") is None
    assert storage.sniff_bytes(GIF) is None
