"""CR-C · Atbildes termiņa pagarināšana (POST /submissions/{id}/extend).

Neatkarīgi testi. Sagaidāmās vērtības ņemtas tikai no tracker/CR-C.md
(pieņemšanas kritēriji un precizējumi), docs/openapi.yaml un lietotāja
apstiprinātajiem lēmumiem, nevis no implementācijas.

Visi dati ir sintētiski.
"""

import logging
from datetime import datetime, timezone

import pytest

from app import clock, storage

UNKNOWN_ID = "IES-2026-999999"
VALID_REASON = "Jāsaņem būvvaldes atzinums"

# Sintētiski, viegli atpazīstami personas dati AC9 pārbaudei.
PII = {
    "personalCode": "32000000199",
    "fullName": "Zigrīda Testere-Neatkarīgā",
    "email": "zigrida.testere@example.com",
    "body": "Unikāls iesnieguma teksts par Ozolu ielas 77 apgaismojumu.",
}


def _seed(
    monkeypatch,
    *,
    received_at: str = "2026-09-25T13:40:00+00:00",
    due_date: str = "2026-10-26",
    status: str = "RECEIVED",
) -> str:
    """Ieliek iesniegumu glabātuvē ar zināmu receivedAt un dueDate.

    Pulksteni iestata uz saņemšanas dienu, lai "šodiena" būtu reālistiska
    (darbinieks pagarina drīz pēc saņemšanas).
    """
    record = storage.add(
        {
            **PII,
            "preferredChannel": "EMAIL",
            "topic": "ROADS",
            "subject": "Apgaismojums Ozolu ielā",
            "status": "RECEIVED",
            "receivedAt": received_at,
            "dueDate": due_date,
            "replyChannel": "EMAIL",
            "reasonCode": None,
        }
    )
    if status != "RECEIVED":
        storage.update_status(record["id"], status)
    now = datetime.fromisoformat(received_at).astimezone(timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: now)
    return record["id"]


def _extend(client, submission_id, new_due_date=None, reason=VALID_REASON, **extra):
    body = {}
    if new_due_date is not None:
        body["newDueDate"] = new_due_date
    if reason is not None:
        body["reason"] = reason
    body.update(extra)
    return client.post(f"/submissions/{submission_id}/extend", json=body)


def _due_date(client, submission_id) -> str:
    response = client.get(f"/submissions/{submission_id}")
    assert response.status_code == 200
    return response.json()["dueDate"]


# ---------------------------------------------------------------- AC1


@pytest.mark.parametrize("status", ["RECEIVED", "IN_PROGRESS"])
def test_crc_ac1_valid_extension_sets_due_date(client, monkeypatch, status):
    sid = _seed(monkeypatch, status=status)

    response = _extend(client, sid, "2026-12-15")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == sid
    assert data["dueDate"] == "2026-12-15"
    assert data["status"] == status
    assert _due_date(client, sid) == "2026-12-15"

    # Precizējums: drīkst pagarināt vairākas reizes, ja katru reizi
    # izpildās kritēriji (jaunais termiņš vēlāks par pašreizējo).
    second = _extend(client, sid, "2027-01-10", reason="Jāsaņem otrs atzinums")
    assert second.status_code == 200
    assert second.json()["dueDate"] == "2027-01-10"
    assert _due_date(client, sid) == "2027-01-10"


def test_crc_ac1_precondition_new_submission_due_in_30_days(
    client, valid_payload, monkeypatch
):
    # Lietotāja lēmums: jauns iesniegums saņem dueDate = receivedAt datums + 30 dienas.
    fixed = datetime(2026, 9, 25, 13, 40, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: fixed)

    created = client.post("/submissions", json=valid_payload)

    assert created.status_code == 201
    assert created.json()["dueDate"] == "2026-10-25"


# ---------------------------------------------------------------- AC2


@pytest.mark.parametrize(
    "received_at, due_date, boundary",
    [
        # Piemērs no kritērija.
        ("2026-09-25T13:40:00+00:00", "2026-10-26", "2027-01-25"),
        # Tā pati diena, tuvu pusnaktij UTC: skaita pēc receivedAt datuma (UTC).
        ("2026-09-25T23:59:59+00:00", "2026-10-26", "2027-01-25"),
        # Precizējums: pārpalikušās dienas pārnes uz nākamo mēnesi.
        ("2026-10-31T08:00:00+00:00", "2026-11-30", "2027-03-03"),
        ("2026-05-31T07:20:00+00:00", "2026-06-30", "2026-10-01"),
    ],
)
def test_crc_ac2_exactly_four_months_is_allowed(
    client, monkeypatch, received_at, due_date, boundary
):
    sid = _seed(monkeypatch, received_at=received_at, due_date=due_date)

    response = _extend(client, sid, boundary)

    assert response.status_code == 200
    assert response.json()["dueDate"] == boundary
    assert _due_date(client, sid) == boundary


# ---------------------------------------------------------------- AC3


@pytest.mark.parametrize(
    "received_at, due_date, too_late",
    [
        ("2026-09-25T13:40:00+00:00", "2026-10-26", "2027-01-26"),
        ("2026-09-25T23:59:59+00:00", "2026-10-26", "2027-01-26"),
        ("2026-10-31T08:00:00+00:00", "2026-11-30", "2027-03-04"),
        ("2026-05-31T07:20:00+00:00", "2026-06-30", "2026-10-02"),
    ],
)
def test_crc_ac3_more_than_four_months_rejected(
    client, monkeypatch, received_at, due_date, too_late
):
    sid = _seed(monkeypatch, received_at=received_at, due_date=due_date)

    response = _extend(client, sid, too_late)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DUE_DATE"
    assert _due_date(client, sid) == due_date


# ---------------------------------------------------------------- AC4


@pytest.mark.parametrize("new_due_date", ["2026-10-26", "2026-10-25", "2026-09-30"])
def test_crc_ac4_not_later_than_current_due_date_rejected(
    client, monkeypatch, new_due_date
):
    sid = _seed(monkeypatch, due_date="2026-10-26")

    response = _extend(client, sid, new_due_date)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DUE_DATE"
    assert _due_date(client, sid) == "2026-10-26"


# ---------------------------------------------------------------- AC5


@pytest.mark.parametrize("status", ["FORWARDED", "ANSWERED", "WITHDRAWN"])
def test_crc_ac5_closed_status_returns_409(client, monkeypatch, status):
    sid = _seed(monkeypatch, status=status)

    response = _extend(client, sid, "2026-12-15")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"
    assert _due_date(client, sid) == "2026-10-26"
    assert all(e["action"] != "EXTEND" for e in storage.list_audit(sid))


# ---------------------------------------------------------------- AC6


def test_crc_ac6_unknown_id_returns_404(client):
    response = _extend(client, UNKNOWN_ID, "2026-12-15")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- AC7


@pytest.mark.parametrize(
    "body, field, issue",
    [
        # Nav lauka.
        ({"reason": VALID_REASON}, "newDueDate", "REQUIRED"),
        ({"newDueDate": "2026-12-15"}, "reason", "REQUIRED"),
        # Nepareizs datuma formāts: atļauts tikai YYYY-MM-DD.
        (
            {"newDueDate": "2026/12/15", "reason": VALID_REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "15.12.2026", "reason": VALID_REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "20261215", "reason": VALID_REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "2026-12-15T00:00:00", "reason": VALID_REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "2026-2-5", "reason": VALID_REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "2026-02-30", "reason": VALID_REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": 1765756800, "reason": VALID_REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        # Iemesls par īsu: < 10 rakstzīmes, neskaitot atstarpes.
        (
            {"newDueDate": "2026-12-15", "reason": "Par īsu!"},
            "reason",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "2026-12-15", "reason": "a b c d e f g h i"},
            "reason",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "2026-12-15", "reason": "          "},
            "reason",
            "INVALID_FORMAT",
        ),
        # Iemesls par garu: > 500 rakstzīmes.
        ({"newDueDate": "2026-12-15", "reason": "x" * 501}, "reason", "TOO_LONG"),
    ],
)
def test_crc_ac7_invalid_request_returns_validation_error(
    client, monkeypatch, body, field, issue
):
    sid = _seed(monkeypatch)

    response = client.post(f"/submissions/{sid}/extend", json=body)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {"field": field, "issue": issue} in error["details"]
    assert _due_date(client, sid) == "2026-10-26"


def test_crc_ac7_reason_length_boundaries_accepted(client, monkeypatch):
    # Robežas, kas jāpieņem: tieši 10 rakstzīmes bez atstarpēm (ar atstarpēm
    # starpā) un tieši 500 rakstzīmes pēc sākuma/beigu atstarpju noņemšanas.
    sid = _seed(monkeypatch)

    ten = _extend(client, sid, "2026-11-15", reason="a b c d e f g h i j")
    assert ten.status_code == 200, ten.json()

    five_hundred = _extend(client, sid, "2026-12-15", reason="  " + "y" * 500 + "  ")
    assert five_hundred.status_code == 200, five_hundred.json()
    assert _due_date(client, sid) == "2026-12-15"


# ---------------------------------------------------------------- AC8


def test_crc_ac8_audit_has_extend_entry_with_reason(client, monkeypatch):
    sid = _seed(monkeypatch)

    assert _extend(client, sid, "2026-12-15", reason=VALID_REASON).status_code == 200
    response = client.get(f"/submissions/{sid}/audit")

    assert response.status_code == 200
    extend_entries = [e for e in response.json() if e["action"] == "EXTEND"]
    assert len(extend_entries) == 1
    assert extend_entries[0]["detail"] == VALID_REASON


# ---------------------------------------------------------------- AC9


@pytest.mark.parametrize(
    "scenario",
    ["success", "invalid_due_date", "invalid_state", "not_found", "validation"],
)
def test_crc_ac9_no_personal_data_in_logs_or_errors(
    client, monkeypatch, caplog, scenario
):
    status = "ANSWERED" if scenario == "invalid_state" else "RECEIVED"
    sid = _seed(monkeypatch, status=status)
    # Tikai pagarināšanas izsaukuma žurnāls, nevis testu datu sagatavošana.
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    if scenario == "success":
        response = _extend(client, sid, "2026-12-15")
        assert response.status_code == 200
    elif scenario == "invalid_due_date":
        response = _extend(client, sid, "2027-01-26")
        assert response.status_code == 400
    elif scenario == "invalid_state":
        response = _extend(client, sid, "2026-12-15")
        assert response.status_code == 409
    elif scenario == "not_found":
        response = _extend(client, UNKNOWN_ID, "2026-12-15")
        assert response.status_code == 404
    else:
        response = _extend(client, sid, "nav-datums", reason="īss")
        assert response.status_code == 400

    log_text = (
        "\n".join(f"{r.getMessage()} {r.args!r}" for r in caplog.records) + caplog.text
    )
    for key, value in PII.items():
        assert value not in log_text, f"{key} žurnālā ({scenario})"
        if scenario != "success":
            assert value not in response.text, f"{key} kļūdas atbildē ({scenario})"
