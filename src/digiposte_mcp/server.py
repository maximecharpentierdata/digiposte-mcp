"""Serveur MCP (stdio) pour le coffre Digiposte personnel.

Configuration par variables d'environnement :
- DIGIPOSTE_ACCESS_TOKEN : token d'accès (Bearer) ;
- DIGIPOSTE_COOKIE : en-tête Cookie de secure.digiposte.fr, optionnel, sert à
  renouveler le token via /rest/security/token ;
- DIGIPOSTE_API_URL, DIGIPOSTE_DOCUMENT_URL : URL de base, optionnelles ;
- DIGIPOSTE_ALLOW_DESTRUCTIVE : "1" pour exposer la suppression définitive.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .client import (
    DEFAULT_API_URL,
    DEFAULT_DOCUMENT_URL,
    LOCATIONS,
    DigiposteClient,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

_client: DigiposteClient | None = None


def get_client() -> DigiposteClient:
    global _client
    if _client is None:
        _client = DigiposteClient(
            access_token=os.environ.get("DIGIPOSTE_ACCESS_TOKEN"),
            cookie=os.environ.get("DIGIPOSTE_COOKIE"),
            api_url=os.environ.get("DIGIPOSTE_API_URL", DEFAULT_API_URL),
            document_url=os.environ.get("DIGIPOSTE_DOCUMENT_URL", DEFAULT_DOCUMENT_URL),
        )
    return _client


def build_server(allow_destructive: bool | None = None) -> MCPServer:
    if allow_destructive is None:
        allow_destructive = os.environ.get("DIGIPOSTE_ALLOW_DESTRUCTIVE") == "1"

    mcp = MCPServer(
        name="digiposte",
        instructions=(
            "Accès au coffre Digiposte personnel de l'utilisateur via une API non "
            "officielle. Les identifiants de dossiers et de documents viennent des "
            "outils de listing ; un folder_id vide désigne la racine."
        ),
    )

    # ---------------------------------------------------------------- lecture

    @mcp.tool(annotations=READ_ONLY)
    async def get_profile(mode: str = "default") -> Any:
        """Profil de l'utilisateur Digiposte (GET /v4/profile).

        mode : "default" ou "without_space_consumption".
        """
        return await get_client().get_profile(mode)

    @mcp.tool(annotations=READ_ONLY)
    async def get_safe_size() -> Any:
        """Espace utilisé dans le coffre (GET /v4/profile/safe/size)."""
        return await get_client().get_profile_safe_size()

    @mcp.tool(annotations=READ_ONLY)
    async def list_folders() -> Any:
        """Arborescence des dossiers (GET /v3/folders)."""
        return await get_client().list_folders()

    @mcp.tool(annotations=READ_ONLY)
    async def list_root_documents() -> Any:
        """Documents à la racine du coffre (GET /v3/documents)."""
        return await get_client().list_documents()

    @mcp.tool(annotations=READ_ONLY)
    async def search_documents(
        folder_id: str = "",
        locations: list[str] | None = None,
        health: bool | None = None,
        shared: bool | None = None,
        read: bool | None = None,
        certified: bool | None = None,
        favorite: bool | None = None,
        user_tags: list[str] | None = None,
    ) -> Any:
        """Liste les documents d'un dossier avec des filtres (POST /v3/documents/search).

        folder_id : identifiant du dossier, vide pour la racine.
        locations : sous-ensemble de INBOX, SAFE, TRASH_INBOX, TRASH_SAFE
        (par défaut INBOX et SAFE).
        Les filtres booléens laissés vides ne sont pas envoyés.
        Résultats triés par titre, 1000 au maximum.
        """
        return await get_client().search_documents(
            folder_id=folder_id,
            locations=locations,
            health=health,
            shared=shared,
            read=read,
            certified=certified,
            favorite=favorite,
            user_tags=user_tags,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_trash() -> dict[str, Any]:
        """Dossiers et documents dans la corbeille."""
        client = get_client()
        return {
            "folders": await client.get_trashed_folders(),
            "documents": await client.get_trashed_documents(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def list_user_tags() -> Any:
        """Étiquettes définies par l'utilisateur (GET /v3/documents/userTags)."""
        return await get_client().user_tags()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def download_document(document_id: str, destination_path: str) -> dict[str, Any]:
        """Télécharge un document et l'écrit sur le disque local.

        destination_path : chemin du fichier à créer. Un fichier existant
        n'est pas écrasé.
        """
        target = Path(destination_path).expanduser()
        if target.exists():
            raise FileExistsError(f"{target} existe déjà")
        result = await get_client().get_document_content(document_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.content)
        return {
            "path": str(target.resolve()),
            "size": len(result.content),
            "content_type": result.content_type,
        }

    @mcp.tool(annotations=READ_ONLY)
    async def list_shares() -> Any:
        """Partages de l'utilisateur (GET /v4/partner/user/shares)."""
        return await get_client().list_shares()

    @mcp.tool(annotations=READ_ONLY)
    async def list_shares_with_documents() -> Any:
        """Partages avec leurs documents (GET /v3/shares/with_documents)."""
        return await get_client().list_shares_with_documents()

    @mcp.tool(annotations=READ_ONLY)
    async def get_share(share_id: str) -> Any:
        """Détail d'un partage (GET /v3/share/{id})."""
        return await get_client().get_share(share_id)

    @mcp.tool(annotations=READ_ONLY)
    async def get_share_documents(share_id: str) -> Any:
        """Documents d'un partage (GET /v3/share/{id}/documents)."""
        return await get_client().get_share_documents(share_id)

    # --------------------------------------------------------------- écriture

    @mcp.tool(annotations=WRITE)
    async def create_folder(name: str, parent_id: str = "") -> Any:
        """Crée un dossier (POST /v3/folder). parent_id vide = racine."""
        return await get_client().create_folder(name, parent_id)

    @mcp.tool(annotations=WRITE)
    async def rename_folder(folder_id: str, name: str) -> Any:
        """Renomme un dossier."""
        return await get_client().rename_folder(folder_id, name)

    @mcp.tool(annotations=WRITE)
    async def rename_document(document_id: str, name: str) -> Any:
        """Renomme un document."""
        return await get_client().rename_document(document_id, name)

    @mcp.tool(annotations=WRITE)
    async def copy_documents(document_ids: list[str]) -> Any:
        """Copie des documents dans leur dossier actuel (POST /v3/documents/copy)."""
        return await get_client().copy_documents(document_ids)

    @mcp.tool(annotations=WRITE)
    async def tag_documents(tags: dict[str, list[str]]) -> str:
        """Étiquette des documents (POST /v3/documents/multiTag).

        tags : {identifiant_document: [étiquettes]}.
        """
        await get_client().multi_tag(tags)
        return "ok"

    @mcp.tool(annotations=WRITE)
    async def move_items(
        destination_folder_id: str,
        document_ids: list[str] | None = None,
        folder_ids: list[str] | None = None,
    ) -> str:
        """Déplace des documents et dossiers vers un dossier (PUT /v3/file/tree/move)."""
        await get_client().move(destination_folder_id, document_ids or [], folder_ids or [])
        return "ok"

    @mcp.tool(annotations=WRITE)
    async def upload_document(
        file_path: str,
        folder_id: str = "",
        name: str | None = None,
        health_document: bool = False,
    ) -> Any:
        """Envoie un fichier local dans le coffre (POST /v3/document).

        folder_id vide = racine. name : titre du document, par défaut le nom
        du fichier.
        """
        path = Path(file_path).expanduser()
        data = path.read_bytes()
        return await get_client().create_document(
            name or path.name, data, folder_id=folder_id, health_document=health_document
        )

    @mcp.tool(annotations=WRITE)
    async def trash_items(
        document_ids: list[str] | None = None,
        folder_ids: list[str] | None = None,
    ) -> str:
        """Met des documents et dossiers à la corbeille (POST /v3/file/tree/trash)."""
        await get_client().trash(document_ids or [], folder_ids or [])
        return "ok"

    @mcp.tool(annotations=WRITE)
    async def create_share(
        title: str,
        start_date: datetime,
        end_date: datetime | None = None,
        security_code: str | None = None,
    ) -> Any:
        """Crée un partage (POST /v3/share). Dates en ISO 8601, converties en UTC."""
        return await get_client().create_share(title, start_date, end_date, security_code)

    @mcp.tool(annotations=WRITE)
    async def set_share_documents(share_id: str, document_ids: list[str]) -> str:
        """Définit les documents d'un partage (PUT /v3/share/{id}/documents)."""
        await get_client().set_share_documents(share_id, document_ids)
        return "ok"

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_share(share_id: str) -> str:
        """Supprime un partage (DELETE /v3/share/{id})."""
        await get_client().delete_share(share_id)
        return "ok"

    if allow_destructive:

        @mcp.tool(annotations=DESTRUCTIVE)
        async def delete_permanently(
            document_ids: list[str] | None = None,
            folder_ids: list[str] | None = None,
        ) -> str:
            """Supprime DÉFINITIVEMENT des documents et dossiers (POST /v3/file/tree/delete)."""
            await get_client().delete(document_ids or [], folder_ids or [])
            return "ok"

    return mcp


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()

__all__ = ["build_server", "main", "LOCATIONS"]
