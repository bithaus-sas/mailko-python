"""What the library knows of Mailko's API ``/v1`` (version ``2026-10-01``).

The limits are Mailko's own (``SendEmailSerializer`` and the settings of the
API): checking them here turns a 4xx into a sentence before the call.
"""

DEFAULT_BASE_URL = "https://api.mailko.co"
API_PREFIX = "/v1"
API_VERSION = "2026-10-01"

IDEMPOTENCY_HEADER = "Idempotency-Key"
VERSION_HEADER = "Mailko-Version"
REQUEST_ID_HEADER = "X-Request-Id"
SIGNATURE_HEADER = "Mailko-Signature"
EVENT_ID_HEADER = "Mailko-Event-Id"
EVENT_TYPE_HEADER = "Mailko-Event-Type"

#: A project's key (``mk_live_…`` sends, ``mk_test_…`` validates and never
#: delivers) or a partner app's (``mkp_live_…``, «Conectar Mailko»).
PROJECT_KEY_PREFIXES = ("mk_live_", "mk_test_")
PARTNER_KEY_PREFIXES = ("mkp_live_", "mkp_test_")

#: The headers a sender may set; Mailko answers any other with 422.
ALLOWED_HEADERS = frozenset(
    {
        "list-unsubscribe",
        "list-unsubscribe-post",
        "list-id",
        "references",
        "in-reply-to",
        "x-entity-ref-id",
        "precedence",
    }
)
#: Recipients of one email, counting «to», «cc» and «bcc».
MAX_RECIPIENTS = 50
MAX_ATTACHMENTS = 20
#: The email's text plus each attachment as base64, as Mailko measures it.
MAX_MESSAGE_BYTES = 10 * 1000 * 1000
MAX_TAGS = 10
MAX_TAG_VALUE = 256
#: A header line and the subject.
MAX_LINE = 998
MAX_CONTENT_TYPE = 100
MAX_FILENAME = 255
#: Emails in one ``POST /emails/batch``.
MAX_BATCH = 100

#: Streams of an email.
TRANSACTIONAL = "transactional"
MARKETING = "marketing"

#: A webhook older than this (seconds) is refused: a captured one replayed.
WEBHOOK_TOLERANCE_SECONDS = 300
