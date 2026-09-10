import asyncio
import sys
from src.core.config import config
from src.core.client import OpenAIClient


async def validate_model(client: OpenAIClient, model_name: str, label: str) -> bool:
    """验证单个模型是否可用。"""
    try:
        request = {
            "model": model_name,
            "messages": [{"role": "user", "content": "Respond with only the word 'ok'"}],
            "max_tokens": 10,
        }
        await client.create_chat_completion(request)
        print(f"  ✅ {label}: {model_name}")
        return True
    except Exception as e:
        print(f"  ❌ {label}: {model_name} - {e}")
        return False


async def validate_all_models():
    """验证所有配置的模型是否可用。"""
    client = OpenAIClient(
        api_key=config.openai_api_key,
        base_url=config.openai_base_url,
        timeout=10,
        api_version=config.azure_api_version,
        custom_headers=config.get_custom_headers(),
    )

    models_to_check = [
        (config.big_model, "BIG_MODEL (opus)"),
        (config.middle_model, "MIDDLE_MODEL (sonnet)"),
        (config.small_model, "SMALL_MODEL (haiku)"),
    ]

    # 去重检查
    seen = set()
    results = []
    for model, label in models_to_check:
        if model in seen:
            results.append((model, label, True))
            continue
        seen.add(model)
        ok = await validate_model(client, model, label)
        results.append((model, label, ok))

    return results


def run_model_validation():
    """同步入口：在启动时验证模型配置。"""
    print()
    print("🔍 Validating model configuration...")
    print(f"   Target: {config.openai_base_url}")

    try:
        results = asyncio.run(validate_all_models())
    except Exception as e:
        print(f"  ⚠️  Model validation failed: {e}")
        print("  ⚠️  Proxy will start anyway, but please check your configuration.")
        return

    all_ok = all(ok for _, _, ok in results)
    failed = [(model, label) for model, label, ok in results if not ok]

    if all_ok:
        print("✅ All models validated successfully.")
    else:
        print(f"⚠️  Some models failed ({len(failed)}/{len(results)}):")
        for model, label in failed:
            print(f"   - {label}: {model}")
        print("   Proxy will start anyway.")
    print()