"""Append rows to a Google Sheet with a service account, no Google SDK needed."""

import json
import logging
import time
from urllib.parse import quote

import httpx
import jwt

from app.core.config import settings

log = logging.getLogger(__name__)
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets"

LEAD_HEADERS = [
    "Date",
    "Entreprise",
    "Nom",
    "Téléphone",
    "E-mail",
    "Besoin",
    "Disponibilité",
    "Langue",
    "Canal",
    "Conversation",
]
INVOICE_HEADERS = [
    "Ajouté le",
    "Fichier",
    "Type",
    "Fournisseur",
    "Matricule fiscal",
    "Numéro",
    "Date",
    "Devise",
    "Total HT",
    "TVA",
    "Timbre",
    "Total TTC",
]
TABS = {"Leads": LEAD_HEADERS, "Factures": INVOICE_HEADERS}

_token: dict = {"value": None, "expires": 0.0}
_ready_tabs: set[str] = set()


def enabled() -> bool:
    return bool(settings.google_service_account_json and settings.google_sheet_id)


def _service_account() -> dict:
    raw = settings.google_service_account_json.strip()
    if not raw.startswith("{"):
        import base64

        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


def _access_token(client: httpx.Client) -> str:
    if _token["value"] and _token["expires"] > time.time() + 60:
        return _token["value"]
    sa = _service_account()
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": sa["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        },
        sa["private_key"],
        algorithm="RS256",
    )
    resp = client.post(
        TOKEN_URL,
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
    )
    resp.raise_for_status()
    body = resp.json()
    _token.update(value=body["access_token"], expires=now + body.get("expires_in", 3600))
    return _token["value"]


def _ensure_tab(client: httpx.Client, headers: dict, tab: str) -> None:
    """Create the tab with its header row the first time we write to it."""
    if tab in _ready_tabs:
        return
    sheet_id = settings.google_sheet_id
    meta = client.get(f"{API}/{sheet_id}", params={"fields": "sheets.properties.title"}, headers=headers)
    meta.raise_for_status()
    titles = {s["properties"]["title"] for s in meta.json().get("sheets", [])}
    if tab not in titles:
        client.post(
            f"{API}/{sheet_id}:batchUpdate",
            json={
                "requests": [
                    {"addSheet": {"properties": {"title": tab, "gridProperties": {"frozenRowCount": 1}}}}
                ]
            },
            headers=headers,
        ).raise_for_status()
    first = client.get(f"{API}/{sheet_id}/values/{quote(tab)}!A1:A1", headers=headers)
    first.raise_for_status()
    if not first.json().get("values"):
        client.put(
            f"{API}/{sheet_id}/values/{quote(tab)}!A1",
            params={"valueInputOption": "RAW"},
            json={"values": [TABS[tab]]},
            headers=headers,
        ).raise_for_status()
    _ready_tabs.add(tab)


def append_row(tab: str, row: list) -> bool:
    if not enabled():
        return False
    try:
        with httpx.Client(timeout=10) as client:
            headers = {"Authorization": f"Bearer {_access_token(client)}"}
            _ensure_tab(client, headers, tab)
            client.post(
                f"{API}/{settings.google_sheet_id}/values/{quote(tab)}!A1:append",
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                json={"values": [["" if v is None else v for v in row]]},
                headers=headers,
            ).raise_for_status()
        return True
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("Google Sheets append to %s failed: %s", tab, exc)
        return False
