from __future__ import annotations

from gateway.config import Settings
from gateway.storage.base import Storage


async def build_storage(settings: Settings) -> Storage:
    if settings.is_live:
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError(
                "SARATHI_MODE=live requires SUPABASE_URL and SUPABASE_SERVICE_KEY. "
                "See docs/HUMAN_TASKS.md."
            )
        from gateway.storage.supabase_store import SupabaseStorage

        store: Storage = SupabaseStorage(settings.supabase_url, settings.supabase_service_key)
    else:
        from gateway.storage.sqlite_store import SQLiteStorage

        store = SQLiteStorage(settings.sqlite_path)

    await store.init()
    return store
