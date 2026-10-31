from digiposte_mcp.server import build_server


async def test_tools_registered():
    names = {t.name for t in await build_server(allow_destructive=False).list_tools()}
    assert "list_folders" in names and "download_document" in names
    assert "delete_permanently" not in names


async def test_destructive_tool_opt_in():
    names = {t.name for t in await build_server(allow_destructive=True).list_tools()}
    assert "delete_permanently" in names
