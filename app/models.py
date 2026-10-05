"""Datu modeļi pēc API līguma (API contract) docs/openapi.yaml."""

import re
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError

# CR-1: 11 cipari vai DDMMYY-NNNNN. Tikai formāts, bez kontrolcipara.
PERSONAL_CODE = re.compile(r"[0-9]{6}-?[0-9]{5}")
# CR-C: datums tikai formātā YYYY-MM-DD (ne laikspiedols, ne datums ar laiku).
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class PreferredChannel(str, Enum):
    EMAIL = "EMAIL"
    POST = "POST"
    E_ADDRESS = "E_ADDRESS"


class ReplyChannel(str, Enum):
    EMAIL = "EMAIL"
    POST = "POST"
    E_ADDRESS = "E_ADDRESS"
    PENDING_CHANNEL_CHECK = "PENDING_CHANNEL_CHECK"


class ReasonCode(str, Enum):
    E_ADDRESS_NOT_ACTIVE = "E_ADDRESS_NOT_ACTIVE"
    REGISTER_UNAVAILABLE = "REGISTER_UNAVAILABLE"


class Topic(str, Enum):
    ROADS = "ROADS"
    WASTE = "WASTE"
    PLANNING = "PLANNING"
    PARKS = "PARKS"
    OTHER = "OTHER"


class TopicItem(BaseModel):
    code: Topic
    name: str


class SubmissionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    IN_PROGRESS = "IN_PROGRESS"
    FORWARDED = "FORWARDED"
    ANSWERED = "ANSWERED"
    WITHDRAWN = "WITHDRAWN"


class SubmissionCreate(BaseModel):
    personalCode: str
    fullName: str
    email: str  # TODO: pārbaudīt e-pasta formātu
    preferredChannel: PreferredChannel
    topic: Topic
    subject: str
    body: str

    @field_validator("personalCode")
    @classmethod
    def normalise_personal_code(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise PydanticCustomError("missing", "Personas kods ir obligāts")
        if not PERSONAL_CODE.fullmatch(value):
            raise PydanticCustomError("invalid_format", "Nepareizs formāts")
        return value.replace("-", "")


class SubmissionCreated(BaseModel):
    id: str
    status: SubmissionStatus
    receivedAt: datetime
    dueDate: date
    replyChannel: ReplyChannel
    reasonCode: ReasonCode | None = None


class Submission(SubmissionCreated, SubmissionCreate):
    pass


class SubmissionListItem(BaseModel):
    """CR-3, 5. kritērijs: tikai šie lauki. Bez vārda un iesnieguma teksta."""

    id: str
    status: SubmissionStatus
    topic: Topic
    receivedAt: datetime
    dueDate: date
    replyChannel: ReplyChannel


# CR-C: vismaz 10 rakstzīmes bez atstarpēm (burti, cipari, simboli).
# Augšējā robeža pēc līguma maxLength: kopējais garums ar atstarpēm.
REASON_MIN_CHARS = 10
REASON_MAX_LENGTH = 500


class ExtendRequest(BaseModel):
    newDueDate: date
    reason: str

    @field_validator("newDueDate", mode="before")
    @classmethod
    def check_date_format(cls, value):
        if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
            raise PydanticCustomError("invalid_format", "Nepareizs datuma formāts")
        return value

    @field_validator("reason")
    @classmethod
    def check_reason_length(cls, value: str) -> str:
        value = value.strip()
        if len(value) > REASON_MAX_LENGTH:
            raise PydanticCustomError("string_too_long", "Iemesls ir par garu")
        if sum(1 for char in value if not char.isspace()) < REASON_MIN_CHARS:
            raise PydanticCustomError("string_too_short", "Iemesls ir par īsu")
        return value


class AuditEntry(BaseModel):
    at: datetime
    action: str
    detail: str | None = None


class Health(BaseModel):
    status: str
    version: str


class ErrorDetail(BaseModel):
    field: str
    issue: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class Error(BaseModel):
    error: ErrorBody
