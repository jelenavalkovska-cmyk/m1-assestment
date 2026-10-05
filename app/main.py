"""Ezermalas pieteikumu sistēma · iesniegumu API (mācību prototips)."""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import clock, omd_client, storage
from app.errors import (
    InvalidDueDate,
    InvalidState,
    SubmissionNotFound,
    register_error_handlers,
)
from app.models import (
    AuditEntry,
    Error,
    ExtendRequest,
    Health,
    PreferredChannel,
    ReasonCode,
    ReplyChannel,
    Submission,
    SubmissionCreate,
    SubmissionCreated,
    SubmissionListItem,
    SubmissionStatus,
    Topic,
    TopicItem,
)

VERSION = "0.1.0"
TOPIC_NAMES = {
    Topic.ROADS: "Ceļi un ielas",
    Topic.WASTE: "Atkritumi",
    Topic.PLANNING: "Teritorijas plānošana",
    Topic.PARKS: "Parki un skvēri",
    Topic.OTHER: "Cits",
}
REPLY_DAYS = 30  # Vienkāršots termiņš: 30 kalendāra dienas
MAX_EXTEND_MONTHS = 4  # CR-C: kopējais termiņš ne garāks par 4 mēnešiem
EXTENDABLE = {SubmissionStatus.RECEIVED.value, SubmissionStatus.IN_PROGRESS.value}
UI_DIR = Path(__file__).resolve().parent.parent / "ui"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ezermala.submissions")

app = FastAPI(title="Ezermalas pieteikumu sistēma · iesniegumu API", version=VERSION)
register_error_handlers(app)
storage.reset()


OmdLookup = Callable[[str], omd_client.OmdCheck]


def get_omd() -> OmdLookup:
    return omd_client.mailbox_status


def decide_reply_channel(
    preferred: PreferredChannel, check: omd_client.OmdCheck
) -> tuple[ReplyChannel, ReasonCode | None]:
    """CR-2: atbildes kanāls pēc OMD atbildes. Ja reģistrs neatbild skaidri, nemin."""
    if check.result is omd_client.OmdResult.ACTIVE:
        return ReplyChannel.E_ADDRESS, None
    if check.result is omd_client.OmdResult.NOT_ACTIVATED:
        if preferred is PreferredChannel.E_ADDRESS:
            return ReplyChannel.EMAIL, ReasonCode.E_ADDRESS_NOT_ACTIVE
        return ReplyChannel(preferred.value), None
    return ReplyChannel.PENDING_CHANNEL_CHECK, ReasonCode.REGISTER_UNAVAILABLE


def add_months(day: date, months: int) -> date:
    """Pieskaita mēnešus. Ja mērķa mēnesī nav tāda datuma, liekās dienas pārnes
    uz nākamo mēnesi: 31. oktobris + 4 mēneši = "31. februāris" = 3. marts."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, 1) + timedelta(days=day.day - 1)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.get("/health", response_model=Health, tags=["Sistēma"])
def get_health() -> Health:
    return Health(status="ok", version=VERSION)


@app.get("/topics", response_model=list[TopicItem], tags=["Klasifikatori"])
def list_topics() -> list[TopicItem]:
    return [TopicItem(code=code, name=name) for code, name in TOPIC_NAMES.items()]


@app.post(
    "/submissions",
    status_code=201,
    response_model=SubmissionCreated,
    responses={400: {"model": Error}},
    tags=["Iesniegumi"],
)
def create_submission(
    data: SubmissionCreate,
    omd: Annotated[OmdLookup, Depends(get_omd)],
) -> SubmissionCreated:
    received_at = clock.now()
    check = omd(data.personalCode)
    reply_channel, reason = decide_reply_channel(data.preferredChannel, check)

    record = storage.add(
        {
            **data.model_dump(mode="json"),
            "status": SubmissionStatus.RECEIVED.value,
            "receivedAt": received_at.isoformat(),
            "dueDate": (received_at.date() + timedelta(days=REPLY_DAYS)).isoformat(),
            "replyChannel": reply_channel.value,
            "reasonCode": reason.value if reason else None,
        }
    )
    storage.add_audit(record["id"], "CREATE")
    # Žurnālā tikai iesnieguma ID un iemesls. Nekad personas kods vai teksts.
    logger.info("Iesniegums saņemts: %s", record["id"])
    if check.result is omd_client.OmdResult.UNAVAILABLE:
        logger.warning(
            "OMD pārbaude neizdevās: iesniegums %s, iemesls %s (%s)",
            record["id"],
            ReasonCode.REGISTER_UNAVAILABLE.value,
            check.detail,
        )
    return SubmissionCreated(**record)


@app.get(
    "/submissions",
    response_model=list[SubmissionListItem],
    tags=["Iesniegumi"],
)
def list_submissions(
    status: SubmissionStatus | None = None, topic: Topic | None = None
) -> list[SubmissionListItem]:
    records = storage.list_submissions(
        status=status.value if status else None,
        topic=topic.value if topic else None,
    )
    return [SubmissionListItem(**record) for record in records]


@app.get(
    "/submissions/{submission_id}",
    response_model=Submission,
    responses={404: {"model": Error}},
    tags=["Iesniegumi"],
)
def get_submission(submission_id: str) -> Submission:
    record = storage.get(submission_id)
    if record is None:
        raise SubmissionNotFound()
    return Submission(**record)


@app.get(
    "/submissions/{submission_id}/audit",
    response_model=list[AuditEntry],
    responses={404: {"model": Error}},
    tags=["Iesniegumi"],
)
def get_submission_audit(submission_id: str) -> list[AuditEntry]:
    if storage.get(submission_id) is None:
        raise SubmissionNotFound()
    return [AuditEntry(**entry) for entry in storage.list_audit(submission_id)]


@app.post(
    "/submissions/{submission_id}/extend",
    response_model=Submission,
    responses={400: {"model": Error}, 404: {"model": Error}, 409: {"model": Error}},
    tags=["Darbības ar iesniegumu"],
)
def extend_submission(submission_id: str, data: ExtendRequest) -> Submission:
    """CR-C: pagarina atbildes termiņu. Robeža: 4 mēneši no receivedAt (UTC)."""
    record = storage.get(submission_id)
    if record is None:
        raise SubmissionNotFound()
    if record["status"] not in EXTENDABLE:
        raise InvalidState()

    received_on = datetime.fromisoformat(record["receivedAt"]).astimezone(UTC).date()
    current_due = date.fromisoformat(record["dueDate"])
    limit = add_months(received_on, MAX_EXTEND_MONTHS)
    earliest_after = max(current_due, clock.now().date())  # ne pagātnē, ne šodien
    if not earliest_after < data.newDueDate <= limit:
        raise InvalidDueDate()

    record = storage.update_due_date(submission_id, data.newDueDate.isoformat())
    storage.add_audit(submission_id, "EXTEND", data.reason)
    # Žurnālā tikai ID un datumi. Iemeslu un personas datus neraksta.
    logger.info(
        "Termiņš pagarināts: %s, %s -> %s",
        submission_id,
        current_due.isoformat(),
        data.newDueDate.isoformat(),
    )
    return Submission(**record)


app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
