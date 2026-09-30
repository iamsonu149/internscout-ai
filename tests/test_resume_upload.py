import json
from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from werkzeug.datastructures import FileStorage

from app.config import Settings
from app.services.resume_upload import (
    ProvidersExhausted,
    extract_pdf,
    parse_resume,
    usable_key,
    validate_output,
)
from tests.test_workspaces import BASE, authenticate, setup

PROFILE = {
    "institution": "Example University",
    "degree": "BSc",
    "graduation_year": 2026,
    "graduation_status": "ongoing",
    "skills": ["Python"],
    "experience_duration": "Summer 2025",
    "desired_roles": ["Software Engineer"],
    "job_type": ["internship"],
    "preferred_work_setup": ["remote"],
    "preferred_locations": [],
    "earliest_start_date": "",
    "full_time_available": False,
    "optional_preferences": {
        "preferred_duration_months": [],
        "willing_to_relocate": False,
        "countries_with_work_authorization": [],
        "compensation_preference": None,
        "minimum_monthly_stipend": None,
        "stipend_currency": "",
        "accept_undisclosed_compensation": False,
    },
}


def pdf(text="Alex Example Python", blank=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if not blank:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output


def reply():
    return httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(PROFILE)}, "finish_reason": "stop"}]}
    )


def test_local_extraction_and_truncation():
    assert "Alex Example" in extract_pdf(FileStorage(pdf(), filename="resume.pdf"))
    assert len(extract_pdf(FileStorage(pdf("a" * 15000), filename="resume.pdf"))) == 12000


@pytest.mark.parametrize(
    "upload",
    [
        None,
        FileStorage(BytesIO(b""), filename="x.pdf"),
        FileStorage(BytesIO(b"bad"), filename="x.pdf"),
        FileStorage(BytesIO(b"%PDF-broken"), filename="x.pdf"),
        FileStorage(pdf(blank=True), filename="x.pdf"),
    ],
)
def test_invalid_pdf(upload):
    with pytest.raises(ValueError):
        extract_pdf(upload)


@pytest.mark.parametrize("key", ["", "your-api-key", "<secret>", "placeholder", "xxxx", "paste_key_here"])
def test_placeholder_keys(key):
    assert not usable_key(key)


def test_fallback_order_and_text_limit():
    models = []

    def handler(request):
        body = json.loads(request.content)
        models.append(body["model"])
        assert len(body["messages"][1]["content"]) == 12000
        if len(models) == 1:
            return httpx.Response(429)
        if len(models) == 2:
            raise httpx.ReadTimeout("timeout")
        if len(models) == 3:
            return httpx.Response(503)
        return reply()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = parse_resume(
            "a" * 15000,
            Settings(gemini_api_key="test", groq_api_key="test", mistral_api_key="test"),
            client=client,
        )
    assert result == PROFILE
    assert models == [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "openai/gpt-oss-20b",
        "mistral-small-latest",
    ]


def test_two_retry_passes():
    calls, delays = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProvidersExhausted):
            parse_resume("text", Settings(groq_api_key="test"), client=client, sleep=delays.append)
    assert len(calls) == 3
    assert delays == [2, 2]


def test_no_keys_and_bad_schema():
    with pytest.raises(ProvidersExhausted):
        parse_resume("text", Settings())
    for raw in [
        "{}",
        "```json\n{}\n```",
        json.dumps({**PROFILE, "skills": "Python"}),
        '{"skills": [], "skills": []}',
    ]:
        with pytest.raises(ValueError):
            validate_output(raw)


def test_model_override_and_retired_github():
    models = []

    def handler(request):
        models.append(json.loads(request.content)["model"])
        assert request.url.host == "api.groq.com"
        return reply()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        settings = Settings(groq_api_key="test", github_token="unused", resume_groq_model="custom-model")
        assert parse_resume("text", settings, client=client) == PROFILE
    assert models == ["custom-model"]
    with pytest.raises(ProvidersExhausted):
        parse_resume("text", Settings(github_token="unused"))


def test_malformed_response_falls_back():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": []}) if len(calls) == 1 else reply()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert parse_resume("text", Settings(gemini_api_key="test"), client=client) == PROFILE
    assert len(calls) == 2


def test_upload_api_and_review_without_saving(monkeypatch):
    app, settings, store = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    monkeypatch.setattr("app.resume_routes.parse_resume", lambda *args: PROFILE)
    monkeypatch.setattr("app.services.resume_upload.parse_resume", lambda *args: PROFILE)
    response = client.post(
        "/api/profile/upload-resume", base_url=BASE, data={"csrf": csrf, "resume": (pdf(), "resume.pdf")}
    )
    assert response.status_code == 200
    assert response.json == PROFILE
    response = client.post(
        "/profile", base_url=BASE, data={"csrf": csrf, "action": "upload", "resume": (pdf(), "resume.pdf")}
    )
    assert response.status_code == 200
    assert b"Example University" in response.data
    assert b"Summer 2025" in response.data
    assert b'Paste your AI output' not in response.data
    assert b'/profile/prompt' not in response.data
    assert b'workspace-resume-upload' in response.data
    assert store.profiles == {}

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.data, "html.parser")
    save_form = soup.select_one('input[value="form_save"]').find_parent("form")
    fields = {}
    for field in save_form.select("input[name], textarea[name], select[name]"):
        if field.name == "textarea":
            value = field.get_text()
        elif field.name == "select":
            selected = field.select_one("option[selected]") or field.select_one("option")
            value = selected.get("value", "")
        else:
            value = field.get("value", "")
        fields[field["name"]] = value
    fields["confirmed"] = "yes"
    assert client.post("/profile", base_url=BASE, data=fields).status_code == 303
    assert store.profiles["alice"]["institution"] == "Example University"
    bob = app.test_client()
    authenticate(bob, settings, "bob")
    assert b"Example University" not in bob.get("/profile", base_url=BASE).data


def test_api_errors():
    app, settings, _ = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    assert client.post("/api/profile/upload-resume", base_url=BASE, data={"csrf": csrf}).status_code == 400
    response = client.post(
        "/api/profile/upload-resume", base_url=BASE, data={"csrf": csrf, "resume": (pdf(), "resume.pdf")}
    )
    assert response.status_code == 503
    assert "error" in response.json
    assert (
        client.post(
            "/api/profile/upload-resume", base_url=BASE, data={"resume": (pdf(), "resume.pdf")}
        ).status_code
        == 400
    )


def test_profile_upload_via_resume_text(monkeypatch):
    """Profile /profile upload action accepts pre-extracted resume_text (client-side PDF extraction path).

    This is the primary code path on Vercel where the edge proxy enforces a
    4.5 MB body-size limit — the binary PDF never leaves the browser, only
    extracted plain text is posted.
    """
    app, settings, store = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    monkeypatch.setattr("app.services.resume_upload.parse_resume", lambda *args: PROFILE)

    # POST extracted text — no file attachment
    response = client.post(
        "/profile",
        base_url=BASE,
        data={
            "csrf": csrf,
            "action": "upload",
            "resume_text": "Alex Example Python Django REST experience intern",
        },
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200, response.data.decode()
    assert b"Example University" in response.data
    assert b"Summer 2025" in response.data
    assert store.profiles == {}  # not saved yet — user must confirm


def test_profile_upload_resume_text_too_long():
    """resume_text longer than MAX_TEXT characters is rejected with a 400."""
    from app.services.resume_upload import MAX_TEXT

    app, settings, _ = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")

    response = client.post(
        "/profile",
        base_url=BASE,
        data={
            "csrf": csrf,
            "action": "upload",
            "resume_text": "x" * (MAX_TEXT + 1),
        },
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 400


def test_profile_upload_prefers_resume_text_over_file(monkeypatch):
    """When both resume_text and a file are present, resume_text takes priority."""
    app, settings, store = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")

    calls = []

    def fake_parse(text, _settings):
        calls.append(text)
        return PROFILE

    monkeypatch.setattr("app.services.resume_upload.parse_resume", fake_parse)

    response = client.post(
        "/profile",
        base_url=BASE,
        data={
            "csrf": csrf,
            "action": "upload",
            "resume_text": "pre-extracted text from browser",
            "resume": (pdf(), "resume.pdf"),
        },
    )
    assert response.status_code == 200
    # parse_resume should have been called with the resume_text value, not PDF bytes
    assert calls and calls[0] == "pre-extracted text from browser"

