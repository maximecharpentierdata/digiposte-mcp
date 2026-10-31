# digiposte-mcp

Serveur [MCP](https://modelcontextprotocol.io) **non officiel** qui donne accès à ton coffre Digiposte personnel : dossiers, documents, étiquettes, partages et corbeille.

## ⚠️ À lire avant d'utiliser

- Digiposte ne publie pas de documentation pour l'API du coffre personnel. Tous les endpoints utilisés ici viennent du code du SDK Go open source [holyhope/digiposte-go-sdk](https://github.com/holyhope/digiposte-go-sdk) (package `v1`, licence MPL 2.0). Aucun endpoint n'a été ajouté en dehors de ceux du SDK.
- Cette API peut changer sans préavis. Le serveur n'a pas encore été testé contre le vrai service : les tests automatiques simulent les réponses HTTP.
- La seule API officielle de Digiposte est l'[API Partenaire v3](https://developer.laposte.fr/catalog-apis/digiposte@3). Elle est réservée aux organisations partenaires et n'est pas utilisée ici.

## Installation

```sh
uv pip install -e .
# ou
pip install -e .
```

## Authentification

Le serveur ne se connecte pas à ta place : il ne gère ni identifiant, ni mot de passe, ni OTP. Il lui faut un token, avec ou sans cookies de session.

| Variable | Rôle |
| --- | --- |
| `DIGIPOSTE_ACCESS_TOKEN` | Token envoyé en `Authorization: Bearer …` |
| `DIGIPOSTE_COOKIE` | Optionnel. Valeur brute de l'en-tête `Cookie` de `secure.digiposte.fr`. Elle permet d'obtenir ou de renouveler le token via `GET https://secure.digiposte.fr/rest/security/token`, comme le fait le SDK. |
| `DIGIPOSTE_API_URL` | Par défaut `https://api.digiposte.fr/api` |
| `DIGIPOSTE_DOCUMENT_URL` | Par défaut `https://secure.digiposte.fr` |
| `DIGIPOSTE_ALLOW_DESTRUCTIVE` | Mettre `1` pour exposer `delete_permanently` |

Il faut au moins `DIGIPOSTE_ACCESS_TOKEN` ou `DIGIPOSTE_COOKIE`.

**Récupérer le token.** Le SDK le lit, après connexion, dans `sessionStorage.getItem("access_token")` sur le site Digiposte. Une fois connecté dans ton navigateur, tape cette expression dans la console des outils de développement. Ce token donne accès à tout ton coffre : ne le partage pas.

## Configuration dans un client MCP

Exemple pour Claude Desktop (`claude_desktop_config.json`) :

```json
{
  "mcpServers": {
    "digiposte": {
      "command": "digiposte-mcp",
      "env": { "DIGIPOSTE_ACCESS_TOKEN": "…" }
    }
  }
}
```

## Outils exposés

Lecture : `get_profile`, `get_safe_size`, `list_folders`, `list_root_documents`, `search_documents`, `list_trash`, `list_user_tags`, `list_shares`, `list_shares_with_documents`, `get_share`, `get_share_documents`, `download_document` (écrit le fichier sur le disque, sans écraser un fichier existant).

Écriture : `create_folder`, `rename_folder`, `rename_document`, `copy_documents`, `tag_documents`, `move_items`, `upload_document`, `trash_items`, `create_share`, `set_share_documents`, `delete_share`.

Destructif, désactivé par défaut : `delete_permanently`.

## Écarts connus avec le SDK

- Les redirections HTTP ne sont jamais suivies. Le SDK, lui, suit celles qui restent sur le même hôte. Une redirection vers `/v3/authorize` est signalée comme une session expirée.
- Sur une réponse 401, si `DIGIPOSTE_COOKIE` est défini, le serveur renouvelle le token une fois puis rejoue la requête.

## Tests

```sh
pip install -e '.[dev]'
pytest
```

## Licence

`src/digiposte_mcp/client.py` est un portage du SDK Go et reste sous licence MPL 2.0, comme l'indique son en-tête. La licence du reste du projet n'est pas encore choisie.
