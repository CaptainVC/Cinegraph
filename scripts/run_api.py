import uvicorn

from cinegraph.config import CinegraphRuntimeSettings


def main() -> None:
    settings = CinegraphRuntimeSettings()
    uvicorn.run(
        "cinegraph.adapters.api.fastapi_app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()
