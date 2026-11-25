from __future__ import annotations

import argparse
import json

from longhao_platform.app import PlatformRuntime
from longhao_platform.contracts import PlatformConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Longhao modular monolith API.")
    parser.add_argument("--check", action="store_true", help="Load contracts/modules and print readiness.")
    parser.add_argument("--port", type=int, help="Override LONGHAO_PORT for this process.")
    args = parser.parse_args(argv)
    config = PlatformConfig.from_env()
    if args.port is not None:
        config = PlatformConfig(
            environment=config.environment,
            bind_host=config.bind_host,
            port=args.port,
            synthetic_providers=config.synthetic_providers,
            allow_missing_modules=config.allow_missing_modules,
            manifest_path=config.manifest_path,
            event_registry_path=config.event_registry_path,
            admin_root=config.admin_root,
        )
    runtime = PlatformRuntime.bootstrap(config)
    status, readiness = runtime.readiness()
    if args.check:
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0 if status == 200 else 1
    import uvicorn

    from longhao_platform.http import create_app

    uvicorn.run(create_app(runtime), host=config.bind_host, port=config.port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
