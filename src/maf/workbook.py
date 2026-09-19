"""
Getting the workbook into Azure OpenAI for this implementation.
================================================================================

The code interpreter tool wants file ids at *construction* time, and
`build_agent` is synchronous - deliberately, because `async with build_agent(...)`
is the thing this sample is trying to teach. The chat client's inner client is
`AsyncAzureOpenAI`, so it cannot do the upload from there.

Rather than make `build_agent` async and rewrite every call site, this builds a
short-lived synchronous client for the one upload. It authenticates exactly like
`build_chat_client`, just with the sync class.

Note that the two implementations do *not* share the uploaded file. Azure OpenAI
and the Foundry project are separate resources with separate file stores, so the
same workbook is uploaded once to each and gets a different id in each.
"""

from __future__ import annotations

from openai import AzureOpenAI

from src.common.workbook import ensure_workbook_uploaded
from src.maf.config import AzureOpenAIConfig

TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


def upload_workbook(cfg: AzureOpenAIConfig) -> str:
    """Upload the contracts workbook, or reuse the copy already there."""
    return ensure_workbook_uploaded(_sync_client(cfg).files)


def _sync_client(cfg: AzureOpenAIConfig) -> AzureOpenAI:
    kwargs = {"azure_endpoint": cfg.endpoint}
    if cfg.api_version:
        kwargs["api_version"] = cfg.api_version
    else:
        # The async client negotiates this; the sync one insists on being told.
        kwargs["api_version"] = "2024-10-21"

    if not cfg.uses_entra_id:
        return AzureOpenAI(api_key=cfg.api_key, **kwargs)

    from azure.identity import (
        AzureCliCredential,
        ChainedTokenCredential,
        DefaultAzureCredential,
        get_bearer_token_provider,
    )

    credential = ChainedTokenCredential(AzureCliCredential(), DefaultAzureCredential())
    return AzureOpenAI(
        azure_ad_token_provider=get_bearer_token_provider(credential, TOKEN_SCOPE),
        **kwargs,
    )
