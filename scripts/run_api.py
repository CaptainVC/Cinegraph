import uvicorn

from cinegraph.config import API_SINGLE_PROCESS_WORKERS, CinegraphRuntimeSettings


def main() -> None:
    settings = CinegraphRuntimeSettings()
    uvicorn.run(
        "cinegraph.adapters.api.fastapi_app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        workers=API_SINGLE_PROCESS_WORKERS,
    )


if __name__ == "__main__":
    main()
