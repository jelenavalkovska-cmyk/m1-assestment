"""CR-C: atbildes termiņa pagarināšana (POST /submissions/{id}/extend)."""

import logging
from datetime import date, datetime, timezone

import pytest

from app import clock, storage
from app.main import add_months

REASON = "Jāsaņem būvvaldes atzinums"


@pytest.fixture
def submission(client, valid_payload, monkeypatch):
    """Iesniegums, saņemts 2026-09-25 (UTC). Sākotnējais termiņš 2026-10-25."""
    fixed = datetime(2026, 9, 25, 13, 40, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: fixed)
    created = client.post("/submissions", json=valid_payload).json()
    assert created["dueDate"] == "2026-10-25"
    return created


def extend(client, submission_id, new_due_date="2026-12-15", reason=REASON):
    return client.post(
        f"/submissions/{submission_id}/extend",
        json={"newDueDate": new_due_date, "reason": reason},
    )


def due_date(client, submission_id):
    return client.get(f"/submissions/{submission_id}").json()["dueDate"]


# 1. kritērijs
@pytest.mark.parametrize("status", ["RECEIVED", "IN_PROGRESS"])
def test_extend_sets_new_due_date(client, submission, status):
    storage.update_status(submission["id"], status)

    response = extend(client, submission["id"], "2026-12-15")

    assert response.status_code == 200
    assert response.json()["dueDate"] == "2026-12-15"
    assert due_date(client, submission["id"]) == "2026-12-15"


def test_extend_can_be_repeated(client, submission):
    assert extend(client, submission["id"], "2026-11-15").status_code == 200
    assert extend(client, submission["id"], "2026-12-15").status_code == 200
    assert due_date(client, submission["id"]) == "2026-12-15"


# 2. kritērijs
def test_extend_exactly_four_months_is_allowed(client, submission):
    response = extend(client, submission["id"], "2027-01-25")

    assert response.status_code == 200
    assert response.json()["dueDate"] == "2027-01-25"


# 3. kritērijs
def test_extend_beyond_four_months_is_rejected(client, submission):
    response = extend(client, submission["id"], "2027-01-26")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DUE_DATE"
    assert due_date(client, submission["id"]) == "2026-10-25"


# 4. kritērijs
@pytest.mark.parametrize("new_due_date", ["2026-10-25", "2026-10-24"])
def test_extend_not_later_than_current_is_rejected(client, submission, new_due_date):
    response = extend(client, submission["id"], new_due_date)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DUE_DATE"
    assert due_date(client, submission["id"]) == "2026-10-25"


# 5. kritērijs
@pytest.mark.parametrize("status", ["FORWARDED", "ANSWERED", "WITHDRAWN"])
def test_extend_in_closed_status_returns_409(client, submission, status):
    storage.update_status(submission["id"], status)

    response = extend(client, submission["id"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"
    assert due_date(client, submission["id"]) == "2026-10-25"


# 6. kritērijs
def test_extend_unknown_id_returns_404(client):
    response = extend(client, "IES-2026-999999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# 7. kritērijs
@pytest.mark.parametrize(
    "body, field, issue",
    [
        ({"reason": REASON}, "newDueDate", "REQUIRED"),
        ({"newDueDate": "2026-12-15"}, "reason", "REQUIRED"),
        (
            {"newDueDate": "15.12.2026", "reason": REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "2026-02-30", "reason": REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        (
            {"newDueDate": "2026-12-15T00:00:00", "reason": REASON},
            "newDueDate",
            "INVALID_FORMAT",
        ),
        ({"newDueDate": 1790000000, "reason": REASON}, "newDueDate", "INVALID_FORMAT"),
        (
            {"newDueDate": "2026-12-15", "reason": "123456789"},
            "reason",
            "INVALID_FORMAT",
        ),
        ({"newDueDate": "2026-12-15", "reason": "x" * 501}, "reason", "TOO_LONG"),
    ],
)
def test_extend_validation_error(client, submission, body, field, issue):
    response = client.post(f"/submissions/{submission['id']}/extend", json=body)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {"field": field, "issue": issue} in error["details"]
    assert due_date(client, submission["id"]) == "2026-10-25"


@pytest.mark.parametrize(
    "reason, status",
    [
        ("1234567890", 200),  # tieši 10
        ("x" * 500, 200),  # tieši 500
        ("a b c d e f g h i", 400),  # 9 rakstzīmes, atstarpes neskaita
        (" " * 20, 400),
        ("a b c d e f g h i j", 200),  # 10 rakstzīmes
        ("x " * 250, 200),  # 499 rakstzīmes kopā ar atstarpēm
        ("x " * 251, 400),  # 501 > līguma maxLength 500
    ],
)
def test_extend_reason_length_ignores_spaces(client, submission, reason, status):
    assert extend(client, submission["id"], reason=reason).status_code == status


# 8. kritērijs
def test_extend_writes_audit_entry(client, submission):
    extend(client, submission["id"], reason=REASON)

    audit = client.get(f"/submissions/{submission['id']}/audit").json()

    assert [entry["action"] for entry in audit] == ["CREATE", "EXTEND"]
    assert audit[-1]["detail"] == REASON


def test_rejected_extend_writes_no_audit_entry(client, submission):
    extend(client, submission["id"], "2027-01-26")

    audit = client.get(f"/submissions/{submission['id']}/audit").json()

    assert [entry["action"] for entry in audit] == ["CREATE"]


# 9. kritērijs
@pytest.mark.parametrize(
    "new_due_date, reason",
    [
        ("2026-12-15", REASON),  # 200
        ("2027-01-26", REASON),  # 400 INVALID_DUE_DATE
        ("2026-12-15", "short"),  # 400 VALIDATION_ERROR
    ],
)
def test_extend_does_not_leak_personal_data(
    client, submission, valid_payload, caplog, new_due_date, reason
):
    caplog.set_level(logging.DEBUG)

    response = extend(client, submission["id"], new_due_date, reason)

    sensitive = [
        valid_payload["personalCode"],
        valid_payload["fullName"],
        valid_payload["email"],
        valid_payload["body"],
    ]
    assert reason not in caplog.text  # iemeslu raksta tikai auditā
    for value in sensitive:
        assert value not in caplog.text
        if response.status_code != 200:
            assert value not in response.text


@pytest.mark.parametrize(
    "start, expected",
    [
        (date(2026, 9, 25), date(2027, 1, 25)),
        (date(2026, 10, 31), date(2027, 3, 3)),  # "31. februāris" = 28.02. + 3 dienas
        (date(2027, 10, 31), date(2028, 3, 2)),  # garais gads: 29.02. + 2 dienas
        (date(2026, 10, 29), date(2027, 3, 1)),  # "29. februāris" = 28.02. + 1 diena
        (date(2026, 5, 31), date(2026, 10, 1)),  # "31. septembris" = 30.09. + 1 diena
        (date(2026, 11, 30), date(2027, 3, 30)),
    ],
)
def test_add_months_overflows_into_next_month(start, expected):
    assert add_months(start, 4) == expected


def test_extend_limit_overflows_into_next_month(client, valid_payload, monkeypatch):
    """Saņemts 31.10.2026: robeža ir 03.03.2027 (iekļauta), 04.03.2027 jau par vēlu."""
    fixed = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: fixed)
    created = client.post("/submissions", json=valid_payload).json()

    late = extend(client, created["id"], "2027-03-04")
    assert late.status_code == 400
    assert late.json()["error"]["code"] == "INVALID_DUE_DATE"

    assert extend(client, created["id"], "2027-03-03").status_code == 200


def test_extend_limit_uses_utc_date_of_received_at(client, valid_payload):
    """01:00 Rīgas laikā 26.09. ir 25.09. pēc UTC, tāpēc robeža ir 25.01.2027."""
    record = storage.add(
        {
            **valid_payload,
            "status": "RECEIVED",
            "receivedAt": "2026-09-26T01:00:00+03:00",
            "dueDate": "2026-10-26",
            "replyChannel": "EMAIL",
            "reasonCode": None,
        }
    )

    assert extend(client, record["id"], "2027-01-26").status_code == 400
    assert extend(client, record["id"], "2027-01-25").status_code == 200


def test_extend_reason_is_stripped_before_length_check(client, submission):
    reason = "   " + "x" * 500 + "   "

    assert extend(client, submission["id"], reason=reason).status_code == 200
    audit = client.get(f"/submissions/{submission['id']}/audit").json()
    assert audit[-1]["detail"] == "x" * 500


@pytest.mark.parametrize(
    "new_due_date, status",
    [
        ("2026-10-04", 400),  # vakar
        ("2026-10-05", 400),  # šodien
        ("2026-10-06", 200),  # rīt
    ],
)
def test_extend_due_date_must_be_after_today(
    client, valid_payload, monkeypatch, new_due_date, status
):
    """Termiņš pagājis (31.07.), robeža 01.11. Šodien 05.10.: tikai no rītdienas."""
    record = storage.add(
        {
            **valid_payload,
            "status": "IN_PROGRESS",
            "receivedAt": "2026-07-01T07:20:00+00:00",
            "dueDate": "2026-07-31",
            "replyChannel": "EMAIL",
            "reasonCode": None,
        }
    )
    today = datetime(2026, 10, 5, 23, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: today)

    response = extend(client, record["id"], new_due_date)

    assert response.status_code == status
    if status == 400:
        assert response.json()["error"]["code"] == "INVALID_DUE_DATE"
        assert due_date(client, record["id"]) == "2026-07-31"
