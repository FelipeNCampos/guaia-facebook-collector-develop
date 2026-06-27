from datetime import datetime, timezone
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import facebook_api_feeder as feeder


def test_normalise_terms_accepts_list_and_string():
    assert feeder.normalise_terms([" Brasil ", "", "politica"]) == "Brasil politica"
    assert feeder.normalise_terms("  Congresso  ") == "Congresso"
    assert feeder.normalise_terms(None) == ""


def test_is_facebook_query_uses_configured_source():
    assert feeder.is_facebook_query({"query_source": "5"})
    assert not feeder.is_facebook_query({"query_source": "2"})
    assert not feeder.is_facebook_query({"query_source": "abc"})


def test_iter_result_records_keeps_order_and_deduplicates_by_source():
    details = {
        "data": {
            "posts": [{"fonte": "https://facebook.com/a", "tipo": "post"}],
            "videos": [{"fonte": "https://facebook.com/a", "tipo": "video"}],
            "profiles": [{"fonte": "https://facebook.com/b", "tipo": "perfil"}],
        }
    }

    records = list(feeder.iter_result_records(details))

    assert records == [
        ("posts", {"fonte": "https://facebook.com/a", "tipo": "post"}),
        ("profiles", {"fonte": "https://facebook.com/b", "tipo": "perfil"}),
    ]


def test_build_send_payload_maps_facebook_contract():
    metadata = {
        "id_query": 123,
        "request_id": "req-1",
        "subject": "Brasil",
        "query_source": 5,
        "source_type": 3,
    }
    record = {
        "id": "post-1",
        "fonte": "https://www.facebook.com/jovempannews/posts/1",
        "tipo": "post",
        "status": "finished",
        "data": "2026-05-25T14:15:45",
        "perfil": "Jovem Pan News",
        "verificado": True,
        "descricao": "Texto coletado",
        "num_comentarios": "4",
        "num_visualizacoes": "10",
        "num_reacoes": "2",
        "media_url": "https://video.xx.fbcdn.net/a.mp4",
        "media_type": "video",
        "drive_file_id": "drive-1",
        "drive_url": "https://drive.google.com/file/d/drive-1/view",
        "ultima_att": datetime(2026, 5, 25, 14, 16, tzinfo=timezone.utc),
    }

    payload = feeder.build_send_payload(123, record, "posts", metadata)

    assert payload["id_query"] == 123
    assert payload["source_type"] == 3
    assert payload["primary_source_name"] == "Jovem Pan News"
    assert payload["publication_text"] == "Texto coletado"
    assert payload["publication_url"] == "https://www.facebook.com/jovempannews/posts/1"
    assert payload["id_target_type"] == "1,5"
    assert payload["social_metadata"]["tipo"] == "post"
    assert payload["social_metadata"]["num_comentarios"] == 4
    assert payload["social_metadata"]["num_visualizacoes"] == 10
    assert payload["social_metadata"]["num_reacoes"] == 2
    assert payload["social_metadata"]["media_url"] == "https://video.xx.fbcdn.net/a.mp4"
    assert payload["social_metadata"]["drive_file_id"] == "drive-1"
    assert payload["social_metadata"]["drive_url"].startswith("https://drive.google.com/")


def test_create_facebook_request_forwards_date_window(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 201

        def json(self):
            return {"metadata": {"id_query": 321, "status_current": 1}, "data": {}}

        def raise_for_status(self):
            raise AssertionError("raise_for_status should not be called")

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(feeder.requests, "post", fake_post)

    result = feeder.create_facebook_request(
        {
            "id_query": 321,
            "start_date": "2026-05-01T00:00:00Z",
            "end_date": "2026-05-31T23:59:59Z",
        },
        "Brasil",
    )

    assert result["metadata"]["id_query"] == 321
    assert captured["url"].endswith("/requests")
    assert captured["json"]["id_query"] == 321
    assert captured["json"]["subject"] == "Brasil"
    assert captured["json"]["status_current"] == 1
    assert captured["json"]["query_source"] == 5
    assert captured["json"]["start_date"] == "2026-05-01T00:00:00+00:00"
    assert captured["json"]["end_date"] == "2026-05-31T23:59:59+00:00"


def test_final_guaia_status_reflects_local_and_send_results():
    assert feeder.final_guaia_status(3, {"total": 1, "enviados": 1, "falhas": 0}) == 3
    assert feeder.final_guaia_status(4, {"total": 1, "enviados": 1, "falhas": 0}) == 3
    assert feeder.final_guaia_status(3, {"total": 0, "enviados": 0, "falhas": 0}) == 3
    assert feeder.final_guaia_status(3, {"total": 1, "enviados": 0, "falhas": 1}) == 3
    assert feeder.final_guaia_status(5, {"total": 1, "enviados": 0, "falhas": 1}) == 3


def test_send_facebook_results_sends_only_new_records(monkeypatch):
    sent_payloads = []

    def fake_safe_send_result(api, **kwargs):
        sent_payloads.append(kwargs)
        return True

    monkeypatch.setattr(feeder, "safe_send_result", fake_safe_send_result)

    details = {
        "metadata": {"id_query": 777, "subject": "Brasil"},
        "data": {
            "posts": [
                {
                    "fonte": "https://facebook.com/a",
                    "descricao": "A",
                    "data": "2026-06-01T00:00:00Z",
                },
                {
                    "fonte": "https://facebook.com/b",
                    "descricao": "B",
                    "data": "2026-06-01T00:00:00Z",
                },
            ]
        },
    }
    sent_keys = {"https://facebook.com/a"}

    summary = feeder.send_facebook_results(None, 777, details, sent_keys=sent_keys)

    assert summary == {"total": 1, "enviados": 1, "falhas": 0}
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["publication_url"] == "https://facebook.com/b"
    assert sent_keys == {"https://facebook.com/a", "https://facebook.com/b"}
