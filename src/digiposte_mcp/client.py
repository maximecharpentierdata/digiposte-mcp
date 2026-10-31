# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Portage Python des appels HTTP du SDK Go open source
# https://github.com/holyhope/digiposte-go-sdk (package v1, licence MPL 2.0).
#
# L'API utilisée ici est celle du coffre Digiposte personnel. Elle n'est pas
# documentée publiquement par Digiposte : chaque endpoint, paramètre et corps
# de requête ci-dessous est repris tel quel du SDK, rien n'a été ajouté.
"""Client HTTP asynchrone pour le coffre Digiposte personnel."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

# Valeurs reprises de settings/settings.go du SDK.
DEFAULT_API_URL = "https://api.digiposte.fr/api"
DEFAULT_DOCUMENT_URL = "https://secure.digiposte.fr"

JSON_CONTENT_TYPE = "application/json"
TRASH_DIR_NAME = "trash"

# Valeurs de l'énumération Location du SDK (document.go).
LOCATIONS = ("INBOX", "SAFE", "TRASH_INBOX", "TRASH_SAFE")

# Valeurs de l'énumération ProfileMode du SDK (profile.go).
PROFILE_MODES = ("default", "without_space_consumption")


class DigiposteError(Exception):
    """Erreur renvoyée par l'API, ou réponse inattendue."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class DigiposteAuthError(DigiposteError):
    """Le token est absent, expiré ou refusé."""


def _path(value: str) -> str:
    """Équivalent de url.PathEscape en Go pour un segment de chemin."""
    return quote(value, safe="")


def _format_time(value: datetime) -> str:
    """Format `2006-01-02T15:04:05Z` utilisé par share.go, en UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class DocumentContent:
    content: bytes
    content_type: str


class DigiposteClient:
    """Client du coffre Digiposte.

    Authentification, comme dans le SDK :
    - le token d'accès est envoyé en ``Authorization: Bearer`` sur toutes les
      requêtes (oauth2.Transport du SDK) ;
    - si des cookies de session de ``secure.digiposte.fr`` sont fournis, un
      nouveau token peut être obtenu via ``GET {document_url}/rest/security/token``
      (TokenSource du SDK, oauth.go).
    """

    def __init__(
        self,
        access_token: str | None = None,
        cookie: str | None = None,
        api_url: str = DEFAULT_API_URL,
        document_url: str = DEFAULT_DOCUMENT_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ):
        if not access_token and not cookie:
            raise DigiposteAuthError(
                "Il faut un token d'accès (DIGIPOSTE_ACCESS_TOKEN) ou des cookies "
                "de session (DIGIPOSTE_COOKIE)."
            )
        self._access_token = access_token or None
        self._cookie = cookie or None
        self.api_url = api_url.rstrip("/")
        self.document_url = document_url.rstrip("/")
        # Le SDK refuse de suivre les redirections vers un autre hôte. On ne
        # suit aucune redirection et on remonte une erreur explicite.
        self._http = httpx.AsyncClient(
            transport=transport, timeout=timeout, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "DigiposteClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ auth

    async def refresh_access_token(self) -> dict[str, Any]:
        """GET {document_url}/rest/security/token (token.go / oauth.go).

        Nécessite les cookies de session. La réponse contient, d'après le SDK,
        ``access_token``, ``expires_at`` et ``is_token_consolidated``.
        """
        if not self._cookie:
            raise DigiposteAuthError(
                "Impossible de renouveler le token sans cookies de session "
                "(DIGIPOSTE_COOKIE)."
            )
        response = await self._http.get(
            f"{self.document_url}/rest/security/token",
            headers={"Cookie": self._cookie},
        )
        data = self._check(response, (200,), expect_json=True)
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise DigiposteAuthError(
                "La réponse de /rest/security/token ne contient pas de access_token.",
                response.status_code,
                data,
            )
        self._access_token = token
        return data

    async def _ensure_token(self) -> None:
        if not self._access_token:
            await self.refresh_access_token()

    def _auth_headers(self, *, with_cookie: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._access_token}"}
        if with_cookie and self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    # --------------------------------------------------------------- helpers

    def _check(self, response: httpx.Response, expected: tuple[int, ...], expect_json: bool) -> Any:
        if response.status_code in expected:
            if not expect_json:
                return None
            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise DigiposteError(
                    f"Réponse JSON invalide de {response.request.url}",
                    response.status_code,
                    response.text,
                ) from exc

        if 300 <= response.status_code < 400:
            location = response.headers.get("Location", "")
            if urlsplit(location).path.endswith("/v3/authorize"):
                raise DigiposteAuthError(
                    "Redirigé vers la page de connexion : le token ou la session a expiré.",
                    response.status_code,
                )
            raise DigiposteError(
                f"Redirection non suivie vers {location!r}", response.status_code
            )

        try:
            body: Any = response.json()
        except json.JSONDecodeError:
            body = response.text
        message = f"HTTP {response.status_code} sur {response.request.method} {response.request.url}"
        # Format d'erreur décrit par RequestErrors dans client.go :
        # une liste de {"error", "error_description", "context"}.
        if isinstance(body, list):
            details = [
                f"{e.get('error')}: {e.get('error_description', '')}"
                for e in body
                if isinstance(e, dict)
            ]
            if details:
                message += " : " + " ; ".join(details)
        cls = DigiposteAuthError if response.status_code == 401 else DigiposteError
        raise cls(message, response.status_code, body)

    async def _api(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, str] | None = None,
        expected: tuple[int, ...] | None = None,
        expect_json: bool = True,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """Requête sur l'API (apiRequest + call dans client.go).

        Comme le SDK : sans résultat attendu, le statut attendu par défaut est
        204, avec un résultat c'est 200.
        """
        await self._ensure_token()
        if expected is None:
            expected = (200,) if expect_json else (204,)

        def build_headers() -> dict[str, str]:
            headers = {"Accept": JSON_CONTENT_TYPE}
            headers["Content-Type"] = JSON_CONTENT_TYPE
            headers.update(self._auth_headers(with_cookie=False))
            if extra_headers:
                headers.update(extra_headers)
            return headers

        kwargs: dict[str, Any] = {"params": params}
        if json_body is not None:
            kwargs["content"] = json.dumps(json_body).encode()
        elif content is not None:
            kwargs["content"] = content

        url = self.api_url + path
        response = await self._http.request(method, url, headers=build_headers(), **kwargs)
        if response.status_code == 401 and self._cookie:
            # Le SDK combine un token réutilisable et le TokenSource basé sur
            # les cookies ; ici on retente une fois après renouvellement.
            await self.refresh_access_token()
            response = await self._http.request(method, url, headers=build_headers(), **kwargs)
        return self._check(response, expected, expect_json)

    # --------------------------------------------------------------- profile

    async def get_profile(self, mode: str = "default") -> Any:
        """GET /v4/profile (profile.go). ``mode`` est omis s'il vaut ``default``."""
        if mode not in PROFILE_MODES:
            raise ValueError(f"mode doit être l'un de {PROFILE_MODES}")
        params = None if mode == "default" else {"mode": mode}
        return await self._api("GET", "/v4/profile", params=params)

    async def get_profile_safe_size(self) -> Any:
        """GET /v4/profile/safe/size (profile.go)."""
        return await self._api("GET", "/v4/profile/safe/size")

    # --------------------------------------------------------------- folders

    async def list_folders(self) -> Any:
        """GET /v3/folders (folder.go)."""
        return await self._api("GET", "/v3/folders")

    async def get_trashed_folders(self) -> Any:
        """GET /v3/folders/trash (folder.go)."""
        return await self._api("GET", f"/v3/folders/{TRASH_DIR_NAME}")

    async def create_folder(self, name: str, parent_id: str = "") -> Any:
        """POST /v3/folder (folder.go). ``parent_id`` vide = racine."""
        return await self._api(
            "POST", "/v3/folder", json_body={"parent_id": parent_id, "name": name}
        )

    async def rename_folder(self, folder_id: str, name: str) -> Any:
        """PUT /v3/folder/{id}/rename/{name} (folder.go)."""
        return await self._api(
            "PUT", f"/v3/folder/{_path(folder_id)}/rename/{_path(name)}"
        )

    # ------------------------------------------------------------- documents

    async def list_documents(self) -> Any:
        """GET /v3/documents (document.go) : documents à la racine."""
        return await self._api("GET", "/v3/documents")

    async def search_documents(
        self,
        folder_id: str = "",
        locations: list[str] | None = None,
        health: bool | None = None,
        shared: bool | None = None,
        read: bool | None = None,
        certified: bool | None = None,
        favorite: bool | None = None,
        user_tags: list[str] | None = None,
    ) -> Any:
        """POST /v3/documents/search (document.go, SearchDocuments).

        Par défaut, comme le SDK : ``locations`` = INBOX et SAFE, et les
        paramètres ``max_results=1000`` et ``sort=TITLE``.
        """
        if locations is None:
            locations = ["INBOX", "SAFE"]
        invalid = [loc for loc in locations if loc not in LOCATIONS]
        if invalid:
            raise ValueError(f"locations invalides {invalid}, valeurs possibles {LOCATIONS}")
        body: dict[str, Any] = {"folder_id": folder_id, "locations": locations}
        for key, value in (
            ("health", health),
            ("document_shared", shared),
            ("document_read", read),
            ("document_certified", certified),
            ("favorite", favorite),
        ):
            if value is not None:
                body[key] = value
        if user_tags is not None:
            body["user_tags"] = user_tags
        return await self._api(
            "POST",
            "/v3/documents/search",
            json_body=body,
            params={"max_results": "1000", "sort": "TITLE"},
        )

    async def get_trashed_documents(self) -> Any:
        """POST /v3/documents/search avec ``user_removal: true`` (document.go)."""
        return await self._api(
            "POST",
            "/v3/documents/search",
            json_body={"user_removal": True},
            params={"max_results": "1000", "sort": "TITLE"},
        )

    async def get_document_content(self, document_id: str) -> DocumentContent:
        """GET {document_url}/rest/content/document/{id} (document.go)."""
        await self._ensure_token()
        url = f"{self.document_url}/rest/content/document/{_path(document_id)}"
        response = await self._http.get(url, headers=self._auth_headers(with_cookie=True))
        if response.status_code == 401 and self._cookie:
            await self.refresh_access_token()
            response = await self._http.get(url, headers=self._auth_headers(with_cookie=True))
        self._check(response, (200,), expect_json=False)
        return DocumentContent(
            content=response.content,
            content_type=response.headers.get("Content-Type", ""),
        )

    async def rename_document(self, document_id: str, name: str) -> Any:
        """PUT /v3/document/{id}/rename/{name} (document.go)."""
        return await self._api(
            "PUT", f"/v3/document/{_path(document_id)}/rename/{_path(name)}"
        )

    async def copy_documents(self, document_ids: list[str]) -> Any:
        """POST /v3/documents/copy (document.go) : copie dans le même dossier."""
        return await self._api(
            "POST", "/v3/documents/copy", json_body={"documents": document_ids}
        )

    async def multi_tag(self, tags: dict[str, list[str]]) -> None:
        """POST /v3/documents/multiTag (document.go). Statut attendu : 200."""
        await self._api(
            "POST",
            "/v3/documents/multiTag",
            json_body={"tags": tags},
            expected=(200,),
            expect_json=False,
        )

    async def user_tags(self) -> Any:
        """GET /v3/documents/userTags (document.go)."""
        return await self._api("GET", "/v3/documents/userTags")

    async def create_document(
        self,
        name: str,
        data: bytes,
        folder_id: str = "",
        health_document: bool = False,
    ) -> Any:
        """POST /v3/document en multipart (document.go, CreateDocument).

        Champs du formulaire repris du SDK : ``health_document``, ``folder_id``
        (si non vide), ``title``, le fichier ``archive`` et ``archive_size``.
        En-tête ``X-Api-Version-Minor: 2`` comme dans le SDK.
        """
        # Même ordre de champs que uploadForm() dans le SDK.
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []

        def field(field_name: str, value: str) -> None:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"\r\n\r\n{value}\r\n'.encode()
            )

        field("health_document", "true" if health_document else "false")
        if folder_id:
            field("folder_id", folder_id)
        field("title", name)
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="archive"; filename="{escaped}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
        field("archive_size", str(len(data)))
        parts.append(f"--{boundary}--\r\n".encode())
        return await self._api(
            "POST",
            "/v3/document",
            content=b"".join(parts),
            extra_headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-Api-Version-Minor": "2",
            },
        )

    # ------------------------------------------------------------- file tree

    async def trash(self, document_ids: list[str], folder_ids: list[str]) -> None:
        """POST /v3/file/tree/trash?check=false (client.go)."""
        await self._api(
            "POST",
            "/v3/file/tree/trash",
            json_body={"document_ids": document_ids, "folder_ids": folder_ids},
            params={"check": "false"},
            expect_json=False,
        )

    async def delete(self, document_ids: list[str], folder_ids: list[str]) -> None:
        """POST /v3/file/tree/delete (client.go) : suppression définitive."""
        await self._api(
            "POST",
            "/v3/file/tree/delete",
            json_body={"document_ids": document_ids, "folder_ids": folder_ids},
            expect_json=False,
        )

    async def move(self, destination_folder_id: str, document_ids: list[str], folder_ids: list[str]) -> None:
        """PUT /v3/file/tree/move?to={id} (client.go)."""
        await self._api(
            "PUT",
            "/v3/file/tree/move",
            json_body={"document_ids": document_ids, "folder_ids": folder_ids},
            params={"to": destination_folder_id},
            expect_json=False,
        )

    # ---------------------------------------------------------------- shares

    async def list_shares(self) -> Any:
        """GET /v4/partner/user/shares (share.go)."""
        return await self._api("GET", "/v4/partner/user/shares")

    async def list_shares_with_documents(self) -> Any:
        """GET /v3/shares/with_documents (share.go)."""
        return await self._api("GET", "/v3/shares/with_documents")

    async def get_share(self, share_id: str) -> Any:
        """GET /v3/share/{id} (share.go)."""
        return await self._api("GET", f"/v3/share/{_path(share_id)}")

    async def get_share_documents(self, share_id: str) -> Any:
        """GET /v3/share/{id}/documents (share.go)."""
        return await self._api("GET", f"/v3/share/{_path(share_id)}/documents")

    async def create_share(
        self,
        title: str,
        start_date: datetime,
        end_date: datetime | None = None,
        security_code: str | None = None,
    ) -> Any:
        """POST /v3/share (share.go)."""
        body: dict[str, Any] = {"start_date": _format_time(start_date), "title": title}
        if end_date is not None:
            body["end_date"] = _format_time(end_date)
        if security_code:
            body["security_code"] = security_code
        return await self._api("POST", "/v3/share", json_body=body)

    async def set_share_documents(self, share_id: str, document_ids: list[str]) -> None:
        """PUT /v3/share/{id}/documents (share.go)."""
        await self._api(
            "PUT",
            f"/v3/share/{_path(share_id)}/documents",
            json_body={"ids": document_ids},
            expect_json=False,
        )

    async def delete_share(self, share_id: str) -> None:
        """DELETE /v3/share/{id} (share.go)."""
        await self._api("DELETE", f"/v3/share/{_path(share_id)}", expect_json=False)
